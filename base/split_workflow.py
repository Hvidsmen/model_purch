"""Snapshot-based scenario workflow; all writes use optimistic revision checks."""
from copy import deepcopy
from datetime import date, timedelta

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from .models import SplitScenario, SplitScenarioEvent, SalesPlanningChannel
from .sales_planning_scope import split_groups
from .sales_planning_group_service import group_goods, block_kind, pair_error
from .split_engine import number, stored, allocate, quantity, planning_months, forecast_months, months_between, evaluate, ZERO, HUNDRED, precise


def group_snapshot(group):
    powers = {}
    for good in group_goods(group):
        power, kind = good.power_class.strip(), block_kind(good.block_type)
        if not power or not kind:
            continue
        powers.setdefault(power, []).append({'id': str(good.pk), 'key': good.source_key, 'name': good.name, 'kind': kind})
    pairs = {pair.power: pair for pair in group.block_pairs.select_related('group', 'indoor', 'outdoor') if not pair_error(pair)}
    result = {}
    for power, models in powers.items():
        pair = pairs.get(power)
        result[power] = {'models': models, 'active': True, 'share': '100' if len(powers) == 1 else None,
                         'factor': '1' if len(powers) == 1 else None, 'indoor_cost_percent': None,
                         'indoor': str(pair.indoor_id) if pair else None, 'outdoor': str(pair.outdoor_id) if pair else None,
                         **{field + '_' + kind: None for field in ('stock', 'prior', 'incoming') for kind in ('indoor', 'outdoor')}}
    return {'ref': {'id': group.pk, 'source_key': group.source_key, 'name': group.name,
                    'related': list(split_groups(group.related_groups.all()).values('pk', 'name')), 'replacements': list(split_groups(group.replacement_groups.all()).values('pk', 'name')),
                    'brand': sorted(set(group_goods(group).values_list('brand', flat=True))), 'active': True},
            'ddp': None, 'buy': '0', 'forecast': {}, 'channels': {}, 'powers': result}


def empty_line(scenario):
    months = planning_months(scenario)
    return {'input': '0', 'fact': None, 'markup': None, 'lock_qty': False, 'lock_markup': False,
            'months': {month: '100' if len(months) == 1 else None for month in months}}


@transaction.atomic
def create_scenario(actor, cleaned):
    scenario = SplitScenario(title=cleaned['title'], year=cleaned['start'].year, start=cleaned['start'], end=cleaned['end'],
        stock_date=cleaned['stock_date'], entry_mode=cleaned.get('entry_mode', 'period'), created_by=actor)
    state = {'schema': 1, 'reference_at': timezone.now().isoformat(), 'source_notes': '', 'groups': {},
             'channels': {str(channel.pk): {'id': channel.pk, 'name': channel.name} for channel in cleaned.get('channels', [])}}
    for group in cleaned.get('groups', []):
        entry = group_snapshot(group)
        entry['channels'] = {cid: empty_line(scenario) for cid in state['channels']}
        entry['forecast'] = {month: None for month in forecast_months(scenario)}
        state['groups'][str(group.pk)] = entry
    scenario.state = state
    scenario.save()
    SplitScenarioEvent.objects.create(scenario=scenario, actor=actor, action='Создан сценарий', revision=1)
    return scenario


@transaction.atomic
def persist(scenario, actor, revision, state, action, *, approved=False):
    if scenario.status != 'draft':
        raise ValidationError('Утверждённая версия неизменяема. Создайте корректировку.')
    fields = {'state': state, 'revision': scenario.revision + 1}
    if approved:
        fields.update(status='approved', approved_at=timezone.now())
    if str(revision) != str(scenario.revision) or not SplitScenario.objects.filter(pk=scenario.pk, revision=scenario.revision, status='draft').update(**fields):
        raise ValidationError('Сценарий изменён в другой вкладке. Обновите страницу перед сохранением.')
    SplitScenarioEvent.objects.create(scenario=scenario, actor=actor, revision=fields['revision'], action=action)


def decimal_input(post, name, *, percentage=False, optional=True):
    return stored(number(post.get(name), optional=optional, maximum=HUNDRED if percentage else number('999999999999')))


def selected(state, post):
    gid, cid = post.get('group', ''), post.get('channel', '')
    if gid not in state['groups'] or cid not in state['channels']:
        raise ValidationError('Выберите группу и канал сценария.')
    return gid, cid, state['groups'][gid]


@precise
def bulk_change(scenario, state, post):
    cid, brand = post.get('channel'), post.get('brand')
    if cid not in state['channels']:
        raise ValidationError('Выберите канал сценария.')
    entries = [group for group in state['groups'].values() if brand in group['ref']['brand']]
    if not entries:
        raise ValidationError('Нет групп выбранного бренда.')
    if any(len(group['ref']['brand']) != 1 for group in entries):
        raise ValidationError('У группы несколько брендов. Уточните классификацию перед массовой корректировкой.')
    target_qty, target_markup = number(post.get('quantity')), number(post.get('markup'))
    fixed = sum((quantity(scenario, group['channels'][cid]) for group in entries if group['channels'][cid]['lock_qty']), ZERO)
    free = [group for group in entries if not group['channels'][cid]['lock_qty']]
    base = [quantity(scenario, group['channels'][cid]) for group in free]
    if target_qty < fixed or (not free and target_qty != fixed) or (free and not sum(base, ZERO) and target_qty != fixed):
        raise ValidationError('Новый итог несовместим с закреплениями или отсутствуют исходные пропорции количества.')
    values = allocate(target_qty - fixed, base) if sum(base, ZERO) else [ZERO] * len(free)
    for group, value in zip(free, values):
        line = group['channels'][cid]
        line['input'] = stored(value + (number(line['fact']) if state.get('volume_mode', scenario.entry_mode) == 'annual' else ZERO))
    fixed_revenue, free_revenue, cost = ZERO, ZERO, ZERO
    for group in entries:
        line = group['channels'][cid]
        ddp, markup = number(group.get('ddp')), number(line.get('markup'))
        if ddp <= 0:
            raise ValidationError('Заполните положительный DDP всех групп бренда.')
        subtotal = quantity(scenario, line) * ddp
        cost += subtotal
        if line['lock_markup']:
            fixed_revenue += subtotal * markup
        else:
            free_revenue += subtotal * markup
    desired = cost * target_markup - fixed_revenue
    if desired < 0 or (not free_revenue and desired != 0):
        raise ValidationError('Новая наценка несовместима с закреплениями или отсутствует база распределения.')
    multiplier = desired / free_revenue if free_revenue else ZERO
    for group in entries:
        line = group['channels'][cid]
        if not line['lock_markup']:
            line['markup'] = stored((number(line['markup']) * multiplier).quantize(number('.000001')))
            number(line['markup'])
    achieved = sum((quantity(scenario, group['channels'][cid]) * number(group['ddp']) * number(group['channels'][cid]['markup']) for group in entries), ZERO)
    if abs(achieved - cost * target_markup) >= number('.005'):
        raise ValidationError('При точности наценки 6 знаков этот итог нельзя получить до цента. Измените целевую наценку или закрепления.')
    return state


def changed_state(scenario, post, *, allow_empty=False):
    state = deepcopy(scenario.state)
    action = post.get('action')
    member_action, _, target = (action or '').partition(':')
    if member_action in ('add_group', 'remove_group', 'add_channel', 'remove_channel', 'add_stock_groups'):
        if scenario.status != 'draft':
            raise ValidationError('Утверждённая версия неизменяема. Создайте корректировку.')
        from django.http import QueryDict
        draft = deepcopy(scenario)
        if post.get('with_lines') == '1':
            inputs = post.copy()
            inputs['action'] = 'lines'
            draft.state = changed_state(scenario, inputs)
        gids, cids = list(draft.state['groups']), list(draft.state['channels'])
        if member_action == 'add_stock_groups':
            from .split_stock_groups import groups_from_stock
            inventory = deepcopy(groups_from_stock(scenario.stock_date))
            added = [row['id'] for row in inventory['groups'] if row['id'] not in gids]
            gids.extend(added)
            inventory['added'] = len(added)
            draft.state['inventory_selection'] = inventory
            composition = QueryDict('', mutable=True)
            composition.update(action='composition', confirm='on')
            composition.setlist('groups', gids)
            composition.setlist('channels', cids)
            return changed_state(draft, composition, allow_empty=True)
        if member_action.startswith('add_'):
            target = post.get('new_group' if member_action == 'add_group' else 'new_channel', '')
        if not target:
            raise ValidationError('Выберите группу или канал для добавления.')
        values = gids if member_action.endswith('group') else cids
        if member_action.startswith('remove_'):
            if target not in values:
                raise ValidationError('Элемент уже отсутствует в плане. Обновите страницу.')
            values.remove(target)
        elif target not in values:
            values.append(target)
        composition = QueryDict('', mutable=True)
        composition.update(action='composition', confirm='on')
        composition.setlist('groups', gids)
        composition.setlist('channels', cids)
        return changed_state(draft, composition, allow_empty=True)
    if action == 'lines':
        for gid, group in state['groups'].items():
            group['ddp'] = decimal_input(post, 'ddp_' + gid)
            for cid, line in group['channels'].items():
                suffix = gid + '_' + cid
                line.update(input=decimal_input(post, 'q_' + suffix, optional=False), fact=decimal_input(post, 'fact_' + suffix),
                    markup=decimal_input(post, 'markup_' + suffix), lock_qty=post.get('lockq_' + suffix) == 'on',
                    lock_markup=post.get('lockm_' + suffix) == 'on')
    elif action in ('distribution', 'distribution_months', 'distribution_powers', 'equal_months', 'equal_powers'):
        gid, cid, group = selected(state, post)
        if action == 'equal_months':
            group['channels'][cid]['allocation'] = 'percent'
            group['channels'][cid]['months'] = dict(zip(planning_months(scenario), map(stored, allocate(HUNDRED, [number(1)] * len(planning_months(scenario))))))
        elif action == 'equal_powers':
            keys = [key for key, power in group['powers'].items() if power['active']]
            if not keys:
                raise ValidationError('Нет мощностей для распределения.')
            for power in group['powers'].values():
                if not power['active']:
                    power['share'] = '0'
            for key, value in zip(keys, allocate(HUNDRED, [number(1)] * len(keys))):
                group['powers'][key]['share'] = stored(value)
        else:
            if action != 'distribution_powers':
                mode = post.get('monthly_mode', 'percent')
                if mode not in ('percent', 'quantity'):
                    raise ValidationError('Выберите доли или месячные количества.')
                group['channels'][cid]['allocation'] = mode
                group['channels'][cid]['month_quantities'] = {
                    month: decimal_input(post, 'qty_' + month) for month in planning_months(scenario)}
                for month in planning_months(scenario):
                    group['channels'][cid]['months'][month] = decimal_input(post, 'month_' + month, percentage=True)
            if action != 'distribution_months':
                for index, (key, power) in enumerate(group['powers'].items()):
                    power['share'] = decimal_input(post, 'share_' + str(index), percentage=True)
                    power['factor'] = decimal_input(post, 'factor_' + str(index))
                    power['indoor_cost_percent'] = decimal_input(post, 'cost_' + str(index), percentage=True)
                    for kind in ('indoor', 'outdoor'):
                        value = post.get(kind + '_' + str(index)) or None
                        if value is not None and not any(model['id'] == value and model['kind'] == kind for model in power['models']):
                            raise ValidationError('Выбранная модель не входит в снимок этой мощности.')
                        power[kind] = value
    elif action == 'stock':
        gid, cid, group = selected(state, post)
        group['buy'] = decimal_input(post, 'buy', optional=False)
        for month in forecast_months(scenario):
            group['forecast'][month] = decimal_input(post, 'forecast_' + month)
        group['forecast_imports'] = {}
        for index, power in enumerate(group['powers'].values()):
            for field in ('stock', 'prior', 'incoming'):
                for kind in ('indoor', 'outdoor'):
                    power[field + '_' + kind] = decimal_input(post, field + '_' + kind + '_' + str(index))
    elif action == 'composition':
        if post.get('confirm') != 'on':
            raise ValidationError('Подтвердите изменение состава сценария.')
        volume_mode = post.get('volume_mode', state.get('volume_mode', scenario.entry_mode))
        if volume_mode not in ('period', 'annual'):
            raise ValidationError('Выберите способ задания объёма.')
        state['volume_mode'] = volume_mode
        gids, cids = set(post.getlist('groups')), set(post.getlist('channels'))
        allowed_groups = {str(group.pk): group for group in split_groups().filter(is_active=True)}
        allowed_channels = {str(channel.pk): channel for channel in SalesPlanningChannel.objects.filter(is_active=True)}
        if ((not gids or not cids) and not allow_empty) or not gids <= set(allowed_groups) | set(state['groups']) or not cids <= set(allowed_channels) | set(state['channels']):
            raise ValidationError('Выберите допустимые группы и каналы. Состав не может быть пустым.')
        archived_groups = state.setdefault('archived_groups', {})
        for key in list(state['groups']):
            if key not in gids:
                archived_groups[key] = state['groups'].pop(key)
        for key in gids:
            if key not in state['groups']:
                state['groups'][key] = archived_groups.pop(key) if key in archived_groups else group_snapshot(allowed_groups[key])
        archived_channels = state.setdefault('archived_channels', {})
        for key in list(state['channels']):
            if key not in cids:
                archived_channels[key] = state['channels'].pop(key)
        for key in cids:
            if key not in state['channels']:
                state['channels'][key] = archived_channels.pop(key) if key in archived_channels else {'id': allowed_channels[key].pk, 'name': allowed_channels[key].name}
        for group in state['groups'].values():
            archive = group.setdefault('archived_lines', {})
            for key in list(group['channels']):
                if key not in cids:
                    archive[key] = group['channels'].pop(key)
            for key in cids:
                if key not in group['channels']:
                    group['channels'][key] = archive.pop(key) if key in archive else empty_line(scenario)
            for month in forecast_months(scenario):
                group['forecast'].setdefault(month, None)
    elif action == 'sources':
        state['source_notes'] = post.get('source_notes', '')[:5000]
    elif action == 'bulk':
        state = bulk_change(scenario, state, post)
    elif action == 'refresh':
        if post.get('confirm') != 'on':
            raise ValidationError('Подтвердите обновление снимка состава. Ручные значения сохранятся.')
        for gid, old in state['groups'].items():
            group = split_groups().filter(pk=gid, is_active=True).first()
            if group is None:
                old['ref']['active'] = False
                continue
            fresh = group_snapshot(group)
            old['ref'] = fresh['ref']
            for key, power in old['powers'].items():
                if key not in fresh['powers']:
                    power['active'], power['models'] = False, []
            for key, power in fresh['powers'].items():
                if key in old['powers']:
                    old['powers'][key].update(models=power['models'], active=True)
                else:
                    old['powers'][key] = power
        state['reference_at'] = timezone.now().isoformat()
    else:
        raise ValidationError('Неизвестное действие.')
    return state


@transaction.atomic
def approve(scenario, actor, revision, acknowledge):
    result = evaluate(scenario)
    if result['errors']:
        raise ValidationError(result['errors'])
    if result['warnings'] and not acknowledge:
        raise ValidationError('Есть дефицит. Подтвердите предупреждения перед утверждением.')
    persist(scenario, actor, revision, deepcopy(scenario.state), 'Утверждён план' + ('; дефицит подтверждён' if acknowledge else ''), approved=True)


@transaction.atomic
def correction(scenario, actor):
    if scenario.status != 'approved':
        raise ValidationError('Корректировка создаётся из утверждённой версии.')
    existing = SplitScenario.objects.filter(family=scenario.family, status='draft').first()
    if existing:
        return existing
    version = SplitScenario.objects.filter(family=scenario.family).aggregate(value=Max('version'))['value'] + 1
    result = SplitScenario.objects.create(family=scenario.family, version=version, title=scenario.title, year=scenario.year,
        start=scenario.start, end=scenario.end, stock_date=scenario.stock_date, entry_mode=scenario.entry_mode,
        created_by=actor, parent=scenario, state=deepcopy(scenario.state))
    SplitScenarioEvent.objects.create(scenario=result, actor=actor, revision=1, action=f'Корректировка версии {scenario.version}')
    return result


def forecast_preview(destination, source, start, end):
    if source.status != 'approved' or start > end or start <= destination.stock_date or end >= destination.start or start < source.start or end > source.end:
        raise ValidationError('Выберите утверждённый сценарий и интервал после даты остатка, до начала плана, внутри периода источника.')
    evaluated = evaluate(source)
    if evaluated['errors']:
        raise ValidationError('В источнике не хватает данных для распределения.')
    totals = {(group['ref']['source_key'], month): ZERO
              for group in source.state['groups'].values() for month in planning_months(source)}
    # Model export has two component rows per kit; count one type only.
    for row in evaluated['shipments']:
        if row['kind'] != 'indoor':
            continue
        key = (row['group_key'], row['month'])
        totals[key] = totals.get(key, ZERO) + row['quantity']
    result = []
    for gid, group in destination.state['groups'].items():
        for month in months_between(start, end):
            first = date.fromisoformat(month + '-01')
            from calendar import monthrange
            last = first.replace(day=monthrange(first.year, first.month)[1])
            left, right = max(first, start), min(last, end)
            denominator = (min(last, source.end) - max(first, source.start)).days + 1
            key = (group['ref']['source_key'], month)
            if key not in totals:
                raise ValidationError('В источнике нет распределённых продаж группы ' + group['ref']['name'] + ' за ' + month)
            value = (totals[key] * number((right-left).days + 1) / number(denominator)).quantize(number('.000001'))
            result.append({'gid': gid, 'group': group['ref']['name'], 'month': month, 'value': value})
    return result



