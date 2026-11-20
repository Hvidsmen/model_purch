"""Stock workflow, independent of price completion; proposals never save implicitly."""
from copy import deepcopy
from decimal import Decimal, ROUND_CEILING
import json

from django.core import signing
from django.core.exceptions import PermissionDenied, ValidationError
from django.utils import timezone

from .access import can_access
from .sales_planning_group_service import group_key
from .split_engine import ZERO, HUNDRED, STEP, allocate, forecast_months, number, precise, quantity, stored, target_stock
from .split_opening import opening_balance
from .split_stock_overview import complete_sum
from .split_supplies import corrections, spread, sql_receipts

SALT = 'split-stock-workspace-v1'
STEPS = ('opening', 'targets', 'supplies')


def percent(value):
    return number(value, optional=True, maximum=Decimal(1000))


def pair_known(power):
    models={str(model['id']):model for model in power.get('models',[])}
    return bool(power.get('active') and all(str(power.get(kind)) in models and models[str(power[kind])]['kind']==kind for kind in ('indoor','outdoor')))


def status(code, message, target=''):
    return {'code': code, 'message': message, 'target': target}


@precise
def presentation(scenario):
    rows = []
    for gid, group in scenario.state['groups'].items():
        opening = opening_balance(scenario, group)
        planned = []
        for line in group['channels'].values():
            try: planned.append(quantity(scenario, line))
            except ValidationError: planned.append(None)
        total = complete_sum(planned)
        plan_set = gid in scenario.state.get('sales_reviewed_groups',[]) or any(number(line.get('input'),optional=True) for line in group['channels'].values())
        if total == ZERO and not plan_set: total = None
        target_percent = percent(group.get('target_stock_percent'))
        target = target_stock(group,total)
        row = {'id': gid, 'group': group, 'opening': opening, 'planned': total,
               'target_percent': target_percent, 'target': target, 'new_supply': number(group.get('buy', '0')),
               'target_source': group.get('target_stock_source') or {}, 'powers': [], 'closing': None,
               'months':[{'key':month,'value':group.get('forecast',{}).get(month)} for month in forecast_months(scenario)]}
        distribution_issue = ''
        try:
            sales = spread(group, total) if total is not None else {}
            purchases = spread(group, row['new_supply'])
            adjusted = corrections(group)
            targets = spread(group,target) if target is not None else {}
        except ValidationError:
            sales = purchases = adjusted = targets = {}
            distribution_issue = 'Нужны доли мощностей 100%'
        for index, (key, power) in enumerate(group['powers'].items()):
            op = next(item for item in opening['rows'] if item['power'] == key)
            current = {'key': key, 'index': index, 'opening': op, 'data': power,
                       'planned': sales.get(key), 'new_supply': purchases.get(key), 'target':targets.get(key)}
            for field in ('stock', 'prior'):
                current[field + '_sets'] = None if any(op[field + '_' + kind] is None for kind in ('indoor','outdoor')) else min(op[field + '_indoor'], op[field + '_outdoor'])
            receipts = [sql_receipts(power, kind) for kind in ('indoor', 'outdoor')]
            current['incoming'] = None if any(value is None for value in receipts) else min(receipts)
            current['incoming_indoor'], current['incoming_outdoor'] = receipts
            balances = [None if any(value is None for value in (op['opening_' + kind], receipts[i], sales.get(key), purchases.get(key), adjusted.get(key))) else
                        op['opening_' + kind] + receipts[i] + adjusted[key] + purchases[key] - sales[key]
                        for i, kind in enumerate(('indoor','outdoor'))]
            current['closing'] = None if any(value is None for value in balances) else min(balances)
            row['powers'].append(current)
        for field in ('stock_sets', 'prior_sets', 'incoming', 'closing'):
            row[field] = complete_sum(power[field] for power in row['powers'])
        op_status = status('ready', 'Остаток рассчитан')
        if not group['powers']: op_status = status('missing', 'Нет мощностей в составе группы', 'distribution')
        elif any(p['stock_sets'] is None or p['prior_sets'] is None for p in row['powers']): op_status = status('missing', 'Нужны остатки и поступления обоих блоков', next((p['key']+':'+field+'_'+kind for p in row['powers'] for field in ('stock','prior') for kind in ('indoor','outdoor') if p['opening'][field+'_'+kind] is None),'blocks'))
        elif any(not pair_known(power) for power in group['powers'].values() if power.get('active') or number(power.get('share'),optional=True)): op_status = status('missing', 'Не настроена пара внутренних и наружных блоков', 'distribution')
        elif opening['forecast_total'] is None: op_status = status('missing', 'Нужен прогноз до начала плана', 'forecast')
        elif opening['issues']: op_status = status('missing', opening['issues'][0], 'forecast')
        elif opening['totals']['sets'] is None: op_status = status('missing', 'Нужны доли мощностей или прогноз по мощностям', 'distribution')
        elif opening['deficit']: op_status = status('deficit', 'Дефицит до начала периода', 'forecast')
        supply_status = op_status
        if op_status['code'] == 'ready':
            if total is None: supply_status = status('missing', 'Проверьте объём продаж', 'plan')
            elif distribution_issue: supply_status = status('missing', distribution_issue, 'distribution')
            elif any(p['incoming'] is None for p in row['powers']): supply_status = status('missing', 'Нужны ожидаемые поступления обоих блоков', next((p['key']+':incoming_'+kind for p in row['powers'] for kind in ('indoor','outdoor') if p['incoming_'+kind] is None),'blocks'))
            elif any(p['closing'] is not None and p['closing'] < 0 for p in row['powers']): supply_status = status('deficit', 'Дефицит по мощности', 'powers')
            elif row['closing'] is None: supply_status = status('missing', 'Недостаточно данных для баланса', 'blocks')
            elif target is not None and any(p['target'] is not None and p['closing'] is not None and p['closing']<p['target'] for p in row['powers']): supply_status = status('deficit', 'Остаток ниже целевого запаса', 'buy')
            else: supply_status = status('ready', 'Баланс рассчитан' if target is None else 'Целевой запас обеспечен')
        row['statuses'] = {'opening': op_status, 'supplies': supply_status,
                           'targets': status('missing','Задайте процент запаса','percent') if target_percent is None else status('ready','Процент задан')}
        source = group.get('forecast_workspace_source') or group.get('forecast_history_source') or {}
        row['forecast_source'] = source
        rows.append(row)
    totals = {field: complete_sum(row[field] for row in rows) for field in ('stock_sets','prior_sets','incoming','planned','new_supply','closing','target')}
    totals['forecast'] = complete_sum(row['opening']['forecast_total'] for row in rows)
    totals['opening'] = complete_sum(row['opening']['totals']['sets'] for row in rows)
    counts = {step: {code: sum(row['statuses'][step]['code'] == code for row in rows) for code in ('ready','missing','deficit')} for step in STEPS}
    return {'rows': rows, 'totals': totals, 'counts': counts, 'total': len(rows)}


def decode(raw):
    if not isinstance(raw, str) or len(raw) > 2_000_000: raise ValidationError('Слишком большой набор изменений.')
    try: value = json.loads(raw)
    except (ValueError, TypeError): raise ValidationError('Некорректный набор изменений.')
    if not isinstance(value, dict): raise ValidationError('Передайте изменения групп.')
    return value


def candidate(scenario, raw, tokens='[]', forecasts='[]', only_empty='1'):
    state = deepcopy(scenario.state)
    if forecasts != '[]':
        from .split_forecast_history import apply as apply_forecasts
        state,_=apply_forecasts(scenario,{'tokens':forecasts,'only_empty':only_empty})
    try: proposals = json.loads(tokens)
    except (ValueError, TypeError): raise ValidationError('Некорректное подтверждение предложения.')
    if not isinstance(proposals, list) or len(proposals) > max(1,2*len(state['groups'])): raise ValidationError('Слишком много предложений.')
    for token in proposals:
        try:
            signed = signing.loads(token, salt=SALT, max_age=86400)
            if signed['identity'] != [scenario.pk, scenario.revision]: raise signing.BadSignature
        except (signing.BadSignature, KeyError, TypeError, ValueError): raise ValidationError('Предложение устарело. Рассчитайте предпросмотр заново.')
        for gid, fields in signed['changes'].items():
            if gid not in state['groups']: raise ValidationError('Группа отсутствует в сценарии.')
            state['groups'][gid].update(fields)
    patches = decode(raw)
    if len(patches) > len(state['groups']): raise ValidationError('Слишком много групп.')
    for gid, fields in patches.items():
        if gid not in state['groups'] or not isinstance(fields, dict): raise ValidationError('Группа отсутствует в сценарии.')
        group = state['groups'][gid]
        modes={('group' if field=='forecast_group' else 'month' if field.startswith('month:') else 'power') for field in fields if field=='forecast_group' or field.startswith('month:') or field.endswith(':forecast_before')}
        if len(modes)>1:
            raise ValidationError('Выберите один способ прогноза: по группе или по мощностям.')
        for field, value in fields.items():
            if not isinstance(value, str): raise ValidationError('Значения должны быть числами или пустой строкой.')
            if field == 'target_stock_percent':
                group[field] = stored(percent(value))
                group['target_stock_source'] = {'kind':'manual','label':'Вручную','at':timezone.now().isoformat()}
            elif field == 'buy':
                group[field] = stored(number(value))
                group['buy_source'] = {'kind':'manual','label':'Вручную'}
            elif field == 'forecast_group':
                group['forecast_before_total'] = stored(number(value, optional=True))
                group['forecast_mode'] = 'group'; group['forecast_imports'] = {}
                group['forecast_workspace_source'] = {'kind':'manual','label':'Вручную'}
            elif field.startswith('month:'):
                month=field[6:]
                if month not in forecast_months(scenario):raise ValidationError('Месяц вне периода прогноза.')
                group.setdefault('forecast',{})[month]=stored(number(value,optional=True))
                group['forecast_mode']='monthly';group['forecast_imports']={}
                group['forecast_workspace_source']={'kind':'manual','label':'Вручную по месяцам'}
            elif ':' in field:
                power_key, parameter = field.split(':', 1)
                if power_key not in group['powers'] or parameter not in ('forecast_before','stock_indoor','stock_outdoor','prior_indoor','prior_outdoor','incoming_indoor','incoming_outdoor'):
                    raise ValidationError('Неизвестное поле мощности.')
                power = group['powers'][power_key]
                if parameter.startswith('incoming_') and 'incoming_sql_' + parameter[9:] in power: raise ValidationError('Поступления из источника меняются через обновление данных.')
                power[parameter] = stored(number(value, optional=True))
                if parameter == 'forecast_before':
                    group['forecast_mode'] = 'powers'; group['forecast_imports'] = {}
                    group['forecast_workspace_source'] = {'kind':'manual','label':'Вручную по мощностям'}
                elif group.get('inventory_source'): group['inventory_source']['manual_adjusted'] = True
            else: raise ValidationError('Неизвестное поле группы.')
    if any((group.get('forecast_history_source') or {}).get('method')=='plan_execution' for group in state['groups'].values()):
        from .split_forecast_plan import validate_state
        validate_state(state)
    return state


def group_ids(scenario, raw):
    try: ids = json.loads(raw)
    except (ValueError, TypeError): raise ValidationError('Выберите группы.')
    if not isinstance(ids,list) or not ids or len(ids) > len(scenario.state['groups']) or any(not isinstance(gid,str) or gid not in scenario.state['groups'] for gid in ids) or len(set(ids)) != len(ids):
        raise ValidationError('Выберите группы текущего сценария без повторов.')
    return ids


@precise
def supply_proposal(scenario, group, row):
    if row['target_percent'] is None: raise ValidationError('Задайте процент целевого запаса; 0% допустимы.')
    if row['planned'] is None: raise ValidationError('Проверьте план продаж.')
    if not group['powers']: raise ValidationError('Нет мощностей в группе.')
    shares = [number(p.get('share'), optional=True) for p in group['powers'].values()]
    if any(v is None for v in shares) or sum(shares,ZERO) != HUNDRED: raise ValidationError('Нужны доли мощностей 100%.')
    if any(share and not pair_known(power) for power,share in zip(group['powers'].values(),shares)):
        raise ValidationError('Выберите активные пары внутренних и наружных блоков для мощностей с долей.')
    targets = allocate(row['target'], shares)
    sales = allocate(row['planned'], shares)
    adjusted = corrections(group)
    needs = []
    for p, sold, target, share in zip(row['powers'], sales, targets, shares):
        available = [None if p['opening']['opening_'+kind] is None or sql_receipts(p['data'],kind) is None else
                     p['opening']['opening_'+kind] + sql_receipts(p['data'],kind) + adjusted[p['key']] for kind in ('indoor','outdoor')]
        if any(v is None for v in available): raise ValidationError('Нужны начальные остатки, прогноз и ожидаемые поступления обоих блоков.')
        need = max(ZERO, sold + target - min(available))
        if need and not share: raise ValidationError('У мощности '+p['key']+' дефицит и нулевая доля. Исправьте распределение.')
        needs.append(need)
    value = max((need * HUNDRED / share for need,share in zip(needs,shares) if share), default=ZERO).quantize(STEP, rounding=ROUND_CEILING)
    # Exact six-place allocation must meet each power, including rounding tails.
    for _ in range(4):
        allocated = allocate(value, shares)
        shortages = [(need-got)*HUNDRED/share for need,got,share in zip(needs,allocated,shares) if share and got<need]
        if not shortages: return number(value)
        value += max(shortages).quantize(STEP, rounding=ROUND_CEILING)
    raise ValidationError('Не удалось распределить поставки с нужной точностью.')


def propose(scenario, post, user):
    ids = group_ids(scenario, post.get('groups','[]'))
    kind = post.get('kind')
    records = {}
    purchase = None
    if kind == 'purchase':
        if not can_access(user,'purchases'): raise PermissionDenied
        from model_purch.models import ScenarioModel, PGGoods
        pk = post.get('purchase','')
        purchase = ScenarioModel.objects.filter(pk=pk if pk.isdecimal() else 0).first()
        if purchase is None: raise ValidationError('Выберите сценарий модели закупок.')
        for name, value in PGGoods.objects.filter(scenario_plan=purchase).values_list('planning_group','percent_stock_end'):
            records.setdefault(group_key(name),set()).add(str(value))
    elif kind != 'supplies': raise ValidationError('Неизвестный расчёт.')
    rows = {row['id']:row for row in presentation(scenario)['rows']}
    result = []
    for gid in ids:
        group, row = scenario.state['groups'][gid], rows[gid]
        item = {'gid':gid,'name':group['ref']['name'],'old':stored(row['target_percent'] if kind=='purchase' else row['new_supply'])}
        try:
            if kind == 'purchase':
                if post.get('only_empty','1') == '1' and row['target_percent'] is not None:
                    item.update(skipped=True,error='Сохранённый процент оставлен без изменений.');result.append(item);continue
                values = records.get(group['ref']['source_key'],set())
                if not values: raise ValidationError('Группа не найдена в сценарии закупок.')
                parsed = {percent((Decimal(value)*HUNDRED).quantize(STEP)) for value in values}
                if len(parsed) != 1: raise ValidationError('У группы разные проценты в закупках; выберите значение вручную.')
                value = parsed.pop()
                change = {'target_stock_percent':stored(value),'target_stock_source':{'kind':'purchase','id':purchase.pk,'label':str(purchase),'from':str(purchase.date_start_plan),'through':str(purchase.date_end_plan),'at':timezone.now().isoformat()}}
            else:
                explicit_plan = gid in scenario.state.get('sales_reviewed_groups',[]) or any(number(line.get('input'),optional=True) for line in group['channels'].values())
                if not explicit_plan: raise ValidationError('Задайте или подтвердите план продаж; явный ноль допустим.')
                value = supply_proposal(scenario,group,row)
                change = {'buy':stored(value),'buy_source':{'kind':'calculated','label':'По целевому запасу','at':timezone.now().isoformat()}}
            token = signing.dumps({'identity':[scenario.pk,scenario.revision],'changes':{gid:change}},salt=SALT,compress=True)
            item.update(value=stored(value),token=token,field='target_stock_percent' if kind=='purchase' else 'buy',source=change.get('target_stock_source',change.get('buy_source')))
            if kind == 'supplies':
                candidate_group = deepcopy(group); candidate_group.update(change)
                draft = deepcopy(scenario); draft.state['groups'] = {gid:candidate_group}
                item['closing'] = stored(presentation(draft)['rows'][0]['closing'])
                item['target'] = stored(row['target'])
                item['warning'] = 'Есть дефицит до начала периода: будущая поставка его не исправляет.' if row['opening']['deficit'] else ''
        except (ValidationError, ArithmeticError, ValueError) as exc:
            item['error'] = ' '.join(exc.messages) if isinstance(exc,ValidationError) else 'Некорректный процент в источнике.'
        result.append(item)
    return result


def serialize(report):
    return {'rows':{row['id']:{'opening':stored(row['opening']['totals']['sets']),'forecast':stored(row['opening']['forecast_total']),
              'stock':stored(row['stock_sets']),'prior':stored(row['prior_sets']),'incoming':stored(row['incoming']),
              'planned':stored(row['planned']),'buy':stored(row['new_supply']),'closing':stored(row['closing']),'target':stored(row['target']),
              'statuses':row['statuses'],'target_percent':stored(row['target_percent']),'target_source':row['target_source'],'forecast_source':row['forecast_source'],'buy_source':row['group'].get('buy_source') or {},'powers':{p['key']:{'opening':stored(p['opening']['sets']),'forecast':stored(p['opening']['forecast']),
                  'stock':stored(p['stock_sets']),'prior':stored(p['prior_sets']),'incoming':stored(p['incoming']),
                  'closing':stored(p['closing']),'planned':stored(p['planned']),'buy':stored(p['new_supply'])} for p in row['powers']}}
              for row in report['rows']},'totals':{key:stored(value) for key,value in report['totals'].items()},'counts':report['counts']}
