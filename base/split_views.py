from copy import deepcopy
from datetime import date
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core import signing
from django.core.paginator import Paginator
from django.db import IntegrityError
from django.http import HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from .access import can_access
from .models import SplitScenario, SalesPlanningChannel
from .sales_planning_scope import split_groups
from .split_forms import ScenarioForm
from .split_engine import evaluate, planning_months, forecast_months
from .split_workflow import create_scenario, changed_state, persist, approve, correction, forecast_preview
from . import split_exports


@login_required
@never_cache
@require_http_methods(['GET', 'POST'])
def scenarios(request):
    if not can_access(request.user, 'admin'):
        raise PermissionDenied
    form = ScenarioForm(request.POST if request.method == 'POST' else None)
    if request.method == 'POST' and form.is_valid():
        scenario = create_scenario(request.user, form.cleaned_data)
        return redirect('split_scenario', pk=scenario.pk)
    return render(request, 'base/split_scenarios.html', {'form': form,
        'page': Paginator(SplitScenario.objects.select_related('created_by'), 25).get_page(request.GET.get('page'))})


def return_to(pk, tab, gid='', cid='', section='months'):
    from urllib.parse import urlencode
    query = {'tab': tab, 'group': gid, 'channel': cid, 'section':section}
    if tab == 'distribution':
        query['edit'] = '1'
    return reverse('split_scenario', args=[pk]) + '?' + urlencode(query)


@login_required
@never_cache
@require_http_methods(['GET', 'POST'])
def scenario(request, pk):
    if not can_access(request.user, 'admin'):
        raise PermissionDenied
    item = get_object_or_404(SplitScenario, pk=pk)
    tab = request.GET.get('tab', 'plan')
    if tab not in ('plan', 'distribution', 'stock', 'sources', 'checks', 'versions', 'detail'):
        tab = 'plan'
    distribution_section = request.GET.get('section', 'months')
    if distribution_section not in ('months','powers'):
        distribution_section = 'months'
    distribution_edit = request.GET.get('edit') == '1' or request.method == 'POST'
    distribution_filters = {key:request.GET.get('filter_'+key,'').strip() for key in ('group','channel','status','q')}
    gid = request.POST.get('group') or request.GET.get('group') or next(iter(item.state['groups']), '')
    cid = request.POST.get('channel') or request.GET.get('channel') or next(iter(item.state['channels']), '')
    if (gid and gid not in item.state['groups']) or (cid and cid not in item.state['channels']):
        return HttpResponseBadRequest('Выберите группу и канал сценария.')
    if request.method == 'POST' and request.POST.get('action') in ('preview_month_grid','save_month_grid','preview_power_grid','save_power_grid'):
        try:
            if item.status != 'draft' or str(item.revision) != str(request.POST.get('revision')):
                raise ValidationError('Расчёт уже изменён или утверждён. Обновите страницу перед сохранением.')
            power_grid = request.POST['action'] in ('preview_power_grid','save_power_grid')
            if power_grid:
                from .split_power_grid import preview as grid_preview, apply_grid
            else:
                from .split_month_grid import preview as grid_preview, apply_grid
            if request.POST['action'].startswith('preview_'):
                return JsonResponse({'ok':True,**grid_preview(item,gid,cid)})
            state,count = apply_grid(item,request.POST.get('grid_payload',''))
            persist(item,request.user,request.POST.get('revision'),state,('Мощности' if power_grid else 'Месяцы')+f' в таблице · сохранено строк: {count}')
            return JsonResponse({'ok':True,'revision':item.revision})
        except ValidationError as exc:
            return JsonResponse({'ok':False,'error':' '.join(exc.messages)},status=400)
    error, preview, forecast = '', None, None
    if request.method == 'POST':
        action = request.POST.get('action')
        try:
            if action == 'correction':
                target = correction(item, request.user)
                return redirect('split_scenario', pk=target.pk)
            if action == 'approve':
                approve(item, request.user, request.POST.get('revision'), request.POST.get('acknowledge') == 'on')
            elif action == 'fact_distribution':
                from .split_distribution_history import resolve_proposal, apply_proposal, metadata
                if item.status != 'draft' or str(item.revision) != str(request.POST.get('revision')):
                    raise ValidationError('Обновите черновик перед загрузкой распределения.')
                state = deepcopy(item.state)
                target = state['groups'].get(gid)
                if not target or cid not in target['channels']:
                    raise ValidationError('Выберите группу и канал.')
                values,basis = resolve_proposal(item,target,state['channels'][cid]['name'],distribution_section,refresh=True)
                apply_proposal(target,cid,distribution_section,values)
                target.setdefault('distribution_sources',{})[distribution_section if distribution_section == 'powers' else 'months:'+cid] = metadata(basis)
                persist(item,request.user,request.POST.get('revision'),state,'Распределение из факта за пять лет · '+distribution_section)
            elif action == 'load_history':
                from .split_history import load_history
                if item.status != 'draft' or str(request.POST.get('revision')) != str(item.revision):
                    raise ValidationError('Обновите черновик. Утверждённый исторический факт не перезаписывается.')
                if not item.state['groups'] or not item.state['channels']:
                    raise ValidationError('Сначала добавьте группы и каналы.')
                post = request.POST.copy(); post['action'] = 'lines'
                state = changed_state(item,post) if post.get('with_lines') == '1' else deepcopy(item.state)
                state['history'] = load_history(item)
                persist(item,request.user,request.POST.get('revision'),state,'Загружен исторический факт за три предыдущих календарных года')
            elif action == 'bulk_preview':
                candidate = deepcopy(item)
                candidate.state = changed_state(item, {**request.POST.dict(), 'action': 'bulk'})
                preview = evaluate(candidate)
            elif action in ('forecast_preview', 'forecast_apply'):
                source = get_object_or_404(SplitScenario, pk=request.POST.get('source') if request.POST.get('source', '').isdecimal() else 0, status='approved')
                try:
                    start, end = date.fromisoformat(request.POST.get('from', '')), date.fromisoformat(request.POST.get('to', ''))
                except ValueError:
                    raise ValidationError('Укажите даты прогноза.')
                forecast = forecast_preview(item, source, start, end)
                if action == 'forecast_apply':
                    state = deepcopy(item.state)
                    for row in forecast:
                        state['groups'][row['gid']]['forecast'][row['month']] = str(row['value'])
                        state['groups'][row['gid']].setdefault('forecast_imports', {})[row['month']] = {
                            'start': str(start), 'end': str(end), 'source': source.pk, 'version': source.version}
                    persist(item, request.user, request.POST.get('revision'), state, f'Прогноз из сценария {source.pk}, v{source.version}; {start}–{end}')
            else:
                state = changed_state(item, request.POST)
                if action in ('equal_months','equal_powers'):
                    source_key = 'months:'+cid if action == 'equal_months' else 'powers'
                    state['groups'][gid].get('distribution_sources',{}).pop(source_key,None)
                if action in ('distribution_months','distribution_powers'):
                    scope = 'months' if action == 'distribution_months' else 'powers'
                    source_key = 'months:'+cid if scope == 'months' else 'powers'
                    sources = state['groups'][gid].setdefault('distribution_sources',{})
                    previous_basis = sources.pop(source_key,None)
                    actual = state['groups'][gid]['channels'][cid]['months'] if scope == 'months' else {key:power['share'] for key,power in state['groups'][gid]['powers'].items() if power['active']}
                    token = request.POST.get('basis_token')
                    if token:
                        try:
                            signed = signing.loads(token,salt='split-distribution',max_age=86400)
                            if signed['identity'] != [item.pk,item.revision,gid,cid,scope]:
                                raise signing.BadSignature
                        except (signing.BadSignature,KeyError,TypeError):
                            raise ValidationError('Основание распределения устарело. Обновите страницу.')
                        from decimal import Decimal
                        adjusted = set(actual) != set(signed['shares']) or any(
                            actual[key] is None or Decimal(actual[key]) != Decimal(signed['shares'][key]) for key in actual)
                        adjusted = adjusted or (scope == 'months' and state['groups'][gid]['channels'][cid].get('allocation') != 'percent')
                        sources[source_key] = {**signed['basis'],'adjusted': adjusted}
                    elif previous_basis:
                        old = item.state['groups'][gid]
                        old_shares = old['channels'][cid]['months'] if scope == 'months' else {key:power['share'] for key,power in old['powers'].items() if power['active']}
                        sources[source_key] = {**previous_basis, 'adjusted': previous_basis.get('adjusted',False) or actual != old_shares or
                            (scope == 'months' and state['groups'][gid]['channels'][cid].get('allocation') != old['channels'][cid].get('allocation'))}
                if action == 'bulk' and request.POST.get('confirm') != 'on':
                    raise ValidationError('Подтвердите применение массовой корректировки.')
                persist(item, request.user, request.POST.get('revision'), state,
                    {'lines': 'Объёмы, наценки и DDP', 'distribution': 'Месяцы, мощности и модели', 'distribution_months':'Распределение по месяцам', 'distribution_powers':'Распределение по мощностям', 'stock': 'Остатки, поступления и прогноз',
                     'equal_months': 'Равномерные доли месяцев вручную', 'equal_powers': 'Равномерные доли мощностей вручную',
                     'refresh': 'Обновлён снимок состава', 'composition': 'Изменён состав групп и каналов', 'bulk': 'Массовая корректировка бренда', 'sources': 'Примечания к источникам',
                     'add_group': 'Добавлена группа в план', 'remove_group': 'Группа исключена из плана',
                     'add_channel': 'Добавлен канал в план', 'remove_channel': 'Канал исключён из плана',
                     'add_stock_groups': 'Добавлены группы из стока и товара в пути'}.get((action or '').split(':')[0], action))
            if preview is None and forecast is None or action == 'forecast_apply':
                if action == 'composition' or (action or '').split(':')[0] in ('add_group', 'remove_group', 'add_channel', 'remove_channel', 'add_stock_groups'):
                    gid, cid = next(iter(state['groups']), ''), next(iter(state['channels']), '')
                if action == 'add_stock_groups':
                    messages.success(request, f"Сток и товар в пути на {item.stock_date:%d.%m.%Y}: найдено групп {len(state['inventory_selection']['groups'])}, добавлено {state['inventory_selection']['added']}. Плановые значения не перезаписаны.")
                else:
                    messages.success(request, 'Изменения сохранены.' if action != 'approve' else 'План утверждён. Редактирование заблокировано.')
                return redirect(return_to(pk, tab, gid, cid, distribution_section))
        except ValidationError as exc:
            error = ' '.join(exc.messages)
        except IntegrityError:
            error = 'Версия уже создана в другой вкладке. Обновите страницу.'
    if request.GET.get('export'):
        try:
            if request.GET['export'] == 'xlsx':
                return split_exports.workbook(item)
            if request.GET['export'] == 'dwh':
                return split_exports.dwh_file(item)
        except ValidationError as exc:
            error = ' '.join(exc.messages)
    data = evaluate(item)
    from .split_history import presentation as history_presentation
    historical = history_presentation(item)
    from .split_matrix_totals import enrich_history, plan_totals, hide_empty_history_years
    hide_empty_history_years(historical)
    historical['latest_year'] = historical['years'][-1] if historical['snapshot'] and historical['years'] else None
    history_total = enrich_history(historical)
    matrix_totals = plan_totals(data, item.state)
    plan_lines = {}
    for line in data['lines']:
        plan_lines.setdefault(line['gid'], {})[line['cid']] = line
    for historical_group in [*historical['groups'], history_total]:
        for year in historical_group['years']:
            year['by_channel'] = dict(zip(item.state['channels'], year['annual']))
            for month in year['months']:
                month['by_channel'] = dict(zip(item.state['channels'], month['cells']))
    group = item.state['groups'].get(gid, {'ref': {}, 'powers': {}, 'channels': {}, 'forecast': {}})
    basis_token, distribution_basis, distribution_warning = '', None, ''
    source_key = 'months:'+cid if distribution_section == 'months' else 'powers'
    distribution_basis = group.get('distribution_sources',{}).get(source_key)
    selected_line = group['channels'].get(cid, {'months': {}})
    detail_rows = [row for row in data['shipments'] if row['group_key'] == group['ref'].get('source_key') and row['channel_id'] == cid]
    monthly = {}
    from decimal import Decimal
    for row in detail_rows:
        total = monthly.setdefault(row['month'], {'month': row['month'], 'quantity': Decimal(0), 'cost': Decimal(0), 'revenue': Decimal(0)})
        if row['kind'] == 'indoor':
            total['quantity'] += row['quantity']
        total['cost'] += row['cost']
        total['revenue'] += row['revenue']
    months = [{'key': month, 'value': selected_line['months'].get(month),
               'quantity': selected_line.get('month_quantities', {}).get(month)} for month in planning_months(item)]
    powers = []
    for index, (key, power) in enumerate(group['powers'].items()):
        powers.append({**power, 'key': key, 'index': index,
            'indoor_options': [model for model in power['models'] if model['kind'] == 'indoor'],
            'outdoor_options': [model for model in power['models'] if model['kind'] == 'outdoor']})
    comparison = None
    distribution_overview = None
    if tab == 'distribution':
        from .split_distribution_overview import overview
        distribution_overview = overview(item,distribution_section,distribution_filters)
    versions = SplitScenario.objects.filter(family=item.family).order_by('-version')
    compare_id = request.GET.get('compare') or (str(item.parent_id) if item.parent_id else '')
    if compare_id:
        reference = get_object_or_404(versions, pk=compare_id if compare_id.isdecimal() else 0)
        old = evaluate(reference)
        old_lines = {(row['gid'], row['cid']): row for row in old['lines']}
        comparison = {'scenario': reference, 'old': old['totals'], 'new': data['totals'], 'rows': [
            {'new': row, 'old': old_lines.get((row['gid'], row['cid']))} for row in data['lines']]}
    return render(request, 'base/split_scenario.html', {'scenario': item, 'state': item.state, 'data': data, 'tab': tab,
        'needs_composition': not item.state['groups'] or not item.state['channels'],
        'entry_mode': item.state.get('volume_mode', item.entry_mode),
        'history': historical,
        'history_total': history_total, 'matrix_totals': matrix_totals, 'plan_lines': plan_lines,
        'history_by_group': {row['gid']: row for row in historical['groups']},
        'plan_channel_colspan': 4 if item.state.get('volume_mode', item.entry_mode) == 'annual' else 3,
        'history_channel_colspan': (4 if item.state.get('volume_mode', item.entry_mode) == 'annual' else 3) + (1 if historical['latest_year'] else 0),
        'history_total_colspan': 1 + (1 if historical['latest_year'] else 0),
        'matrix_colspan': 3 + (4 if item.state.get('volume_mode',item.entry_mode) == 'annual' else 3) * len(item.state['channels']) + ((len(item.state['channels'])+1) if historical['latest_year'] else 0),
        'error': error, 'preview': preview, 'forecast_preview': forecast, 'post': request.POST,
        'gid': gid, 'cid': cid, 'group': group, 'months': months, 'powers': powers,
        'distribution_section':distribution_section, 'distribution_basis':distribution_basis,
        'distribution_overview':distribution_overview,'distribution_edit':distribution_edit,'distribution_filters':distribution_filters,
        'basis_token':basis_token, 'distribution_warning':distribution_warning,
        'monthly_results': [monthly[key] for key in sorted(monthly)],
        'detail_page': Paginator(detail_rows, 100).get_page(request.GET.get('page')),
        'composition_groups': {**{str(group.pk): {'name': group.name} for group in split_groups().filter(is_active=True)},
                               **{key: {'name': group['ref']['name']} for key, group in item.state['groups'].items()}},
        'composition_channels': {**{str(channel.pk): {'name': channel.name} for channel in SalesPlanningChannel.objects.filter(is_active=True)}, **item.state['channels']},
        'forecasts': [{'key': month, 'value': group['forecast'].get(month)} for month in forecast_months(item)],
        'brands': sorted({brand for group in item.state['groups'].values() for brand in group['ref']['brand'] if brand}),
        'forecast_sources': SplitScenario.objects.filter(status='approved', start__lt=item.start).order_by('-start'),
        'versions': versions, 'comparison': comparison, 'events': item.events.select_related('actor').order_by('-at')[:100]})




