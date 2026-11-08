from copy import deepcopy
from datetime import date
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import IntegrityError
from django.http import HttpResponseBadRequest
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
    form = ScenarioForm(request.POST or None, initial={'year': date.today().year + 1, 'mode': 'year', 'entry_mode': 'period'})
    if request.method == 'POST' and form.is_valid():
        scenario = create_scenario(request.user, form.cleaned_data)
        return redirect('split_scenario', pk=scenario.pk)
    return render(request, 'base/split_scenarios.html', {'form': form,
        'page': Paginator(SplitScenario.objects.select_related('created_by'), 25).get_page(request.GET.get('page'))})


def return_to(pk, tab, gid='', cid=''):
    from urllib.parse import urlencode
    return reverse('split_scenario', args=[pk]) + '?' + urlencode({'tab': tab, 'group': gid, 'channel': cid})


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
    gid = request.POST.get('group') or request.GET.get('group') or next(iter(item.state['groups']))
    cid = request.POST.get('channel') or request.GET.get('channel') or next(iter(item.state['channels']))
    if gid not in item.state['groups'] or cid not in item.state['channels']:
        return HttpResponseBadRequest('Выберите группу и канал сценария.')
    error, preview, forecast = '', None, None
    if request.method == 'POST':
        action = request.POST.get('action')
        try:
            if action == 'correction':
                target = correction(item, request.user)
                return redirect('split_scenario', pk=target.pk)
            if action == 'approve':
                approve(item, request.user, request.POST.get('revision'), request.POST.get('acknowledge') == 'on')
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
                if action == 'bulk' and request.POST.get('confirm') != 'on':
                    raise ValidationError('Подтвердите применение массовой корректировки.')
                persist(item, request.user, request.POST.get('revision'), state,
                    {'lines': 'Объёмы, наценки и DDP', 'distribution': 'Месяцы, мощности и модели', 'stock': 'Остатки, поступления и прогноз',
                     'equal_months': 'Равномерные доли месяцев вручную', 'equal_powers': 'Равномерные доли мощностей вручную',
                     'refresh': 'Обновлён снимок состава', 'composition': 'Изменён состав групп и каналов', 'bulk': 'Массовая корректировка бренда', 'sources': 'Примечания к источникам'}.get(action, action))
            if preview is None and forecast is None or action == 'forecast_apply':
                if action == 'composition':
                    gid, cid = next(iter(state['groups'])), next(iter(state['channels']))
                messages.success(request, 'Изменения сохранены.' if action != 'approve' else 'План утверждён. Редактирование заблокировано.')
                return redirect(return_to(pk, tab, gid, cid))
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
    group = item.state['groups'][gid]
    detail_rows = [row for row in data['shipments'] if row['group_key'] == group['ref']['source_key'] and row['channel_id'] == cid]
    monthly = {}
    from decimal import Decimal
    for row in detail_rows:
        total = monthly.setdefault(row['month'], {'month': row['month'], 'quantity': Decimal(0), 'cost': Decimal(0), 'revenue': Decimal(0)})
        if row['kind'] == 'indoor':
            total['quantity'] += row['quantity']
        total['cost'] += row['cost']
        total['revenue'] += row['revenue']
    months = [{'key': month, 'value': group['channels'][cid]['months'].get(month),
               'quantity': group['channels'][cid].get('month_quantities', {}).get(month)} for month in planning_months(item)]
    powers = []
    for index, (key, power) in enumerate(group['powers'].items()):
        powers.append({**power, 'key': key, 'index': index,
            'indoor_options': [model for model in power['models'] if model['kind'] == 'indoor'],
            'outdoor_options': [model for model in power['models'] if model['kind'] == 'outdoor']})
    comparison = None
    versions = SplitScenario.objects.filter(family=item.family).order_by('-version')
    compare_id = request.GET.get('compare') or (str(item.parent_id) if item.parent_id else '')
    if compare_id:
        reference = get_object_or_404(versions, pk=compare_id if compare_id.isdecimal() else 0)
        old = evaluate(reference)
        old_lines = {(row['gid'], row['cid']): row for row in old['lines']}
        comparison = {'scenario': reference, 'old': old['totals'], 'new': data['totals'], 'rows': [
            {'new': row, 'old': old_lines.get((row['gid'], row['cid']))} for row in data['lines']]}
    return render(request, 'base/split_scenario.html', {'scenario': item, 'state': item.state, 'data': data, 'tab': tab,
        'error': error, 'preview': preview, 'forecast_preview': forecast, 'post': request.POST,
        'gid': gid, 'cid': cid, 'group': group, 'months': months, 'powers': powers,
        'monthly_results': [monthly[key] for key in sorted(monthly)],
        'detail_page': Paginator(detail_rows, 100).get_page(request.GET.get('page')),
        'composition_groups': {**{str(group.pk): {'name': group.name} for group in split_groups().filter(is_active=True)},
                               **{key: {'name': group['ref']['name']} for key, group in item.state['groups'].items()}},
        'composition_channels': {**{str(channel.pk): {'name': channel.name} for channel in SalesPlanningChannel.objects.filter(is_active=True)}, **item.state['channels']},
        'forecasts': [{'key': month, 'value': group['forecast'].get(month)} for month in forecast_months(item)],
        'brands': sorted({brand for group in item.state['groups'].values() for brand in group['ref']['brand'] if brand}),
        'forecast_sources': SplitScenario.objects.filter(status='approved', start__lt=item.start).order_by('-start'),
        'versions': versions, 'comparison': comparison, 'events': item.events.select_related('actor').order_by('-at')[:100]})
