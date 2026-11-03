"""Independent 2026 calculation and scoped report; main result tables stay intact."""
import logging
from decimal import Decimal
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import FileResponse
from django.shortcuts import render, redirect
from django.urls import reverse
from django.views.decorators.http import require_http_methods
from django.core.paginator import Paginator
from base.access import can_access, role_for, REPORT_ROLES
from base.report_access import politician_scope, scoped_values
from .models import MotivationCalculationSchedule, MotivationExecution, Subdivision
from .services import reports_next
from .services.calculation_variants import ALL_2026, main_runs
from .services.reports_next_presenter import present


def selection(user, query):
    scope = politician_scope(user)
    if role_for(user, 'motivation') in REPORT_ROLES and not scope:
        raise PermissionDenied
    allowed = scope['type'] if scope else ['Политики', 'Продажи', 'Директора']
    allowed = [allowed] if isinstance(allowed, str) else allowed
    kinds = reports_next.selected_types(query, allowed)
    periods = list(dict.fromkeys(query.getlist('period'))) or list('12345')
    if any(code not in '12345' for code in periods) or query.get('year', '2026') != '2026':
        raise ValidationError('Дополнительный вариант: только 2026 год и кварталы I–IV/год.')
    if query.get('currency', 'USD') not in ('USD', 'RUB'):
        raise ValidationError('Выберите USD или RUB.')
    if scope:
        allowed_branches = scope['subdivision'] if isinstance(scope['subdivision'], list) else [scope['subdivision']]
        if any(branch not in allowed_branches for branch in query.getlist('subdivision')):
            raise PermissionDenied
    return scope, kinds, periods


def data_for(user, execution, query):
    scope, kinds, periods = selection(user, query)
    rows, plan_fact = [], []
    for kind in kinds:
        values = {'TypeCoeff_': kind, 'Subdivision': query.getlist('subdivision'), 'DealAuthor': query.getlist('author'),
                  'gp_sales': [], 'GPGoods': [], 'Brend': [], 'Years': ['2026'], 'Currency': query.get('currency', 'USD')}
        if scope:
            values = scoped_values('manager-detail', values, scope)
        data = reports_next.load_report('manager-detail', values, access_scope=scope, premium_period=periods,
                                        summary_only=True, calculation_variant=ALL_2026)
        from .services.reports import number, ratio
        for item in data.get('motivation_performance', []):
            plan, fact = number(item.get('Plan_')), number(item.get('Fact_'))
            plan_fact.append({'kind': kind, 'subdivision': item['Подразделение'],
                              'year': item['Год'], 'period': item['КварталМотивации'],
                              'formula': item.get('VariationCalculate') or 'Подразделение',
                              'scope_author': item.get('ScopeAuthor') or 'Всё подразделение',
                              'plan': plan, 'fact': fact, 'ratio': ratio(fact, plan)})
        summary = data['period_summary']
        for item in summary['rows']:
            for index, column in enumerate(summary['columns']):
                amount = item['values'][index]
                if amount is None:
                    continue
                target = query.copy()
                target.setlist('period', [reports_next.period_code(column['label'])])
                target.setlist('type', [kind]); target.setlist('author', [item['author']]); target.setlist('subdivision', [item['subdivision']])
                rows.append({'subdivision': item['subdivision'], 'author': item['author'], 'unallocated': item.get('unallocated', False),
                    'kind': kind, 'period': column['label'], 'code': reports_next.period_code(column['label']),
                    'amount': amount, 'bonus': Decimal(0), 'total': amount, 'currency': values['Currency'],
                    'possible': item.get('possible_values', [None] * len(summary['columns']))[index],
                    'detail_url': reverse('motivation_report_all_2026') + '?' + target.urlencode()})
    return {'rows': rows, 'plan_fact': plan_fact, 'kinds': kinds, 'has_unallocated': any(row['unallocated'] for row in rows),
            'total': sum((row['amount'] for row in rows), Decimal(0)), 'currency': query.get('currency', 'USD')}


@require_http_methods(['GET', 'POST'])
def report(request):
    from .operation_views import JobForm
    from .services import calculation_jobs
    from .services.execution_tracking import observe
    scope = politician_scope(request.user)
    if role_for(request.user, 'motivation') in REPORT_ROLES and not scope:
        raise PermissionDenied
    can_start = can_access(request.user, 'motivation_admin')
    query = request.GET.copy(); query['year'] = '2026'
    for field in ('subdivision', 'type', 'author', 'period'):
        values = [value for value in query.getlist(field) if value]
        if values:
            query.setlist(field, values)
        else:
            query.pop(field, None)
    query.pop('execution', None)
    try:
        selection(request.user, query)
    except ValidationError as exc:
        from django.http import HttpResponseBadRequest
        return HttpResponseBadRequest(' '.join(exc.messages))
    initial = {'name': 'Все кварталы 2026', 'variant': ALL_2026, 'fact_year': 2026, 'mode': 'manual',
               'quarter_share': 80, 'year_share': 20, 'payout_threshold': '.8'}
    baseline = main_runs().filter(state='completed', parameters__fact_year=2026).order_by('-completed_at', '-pk').first()
    if baseline:
        for key in ('plan_version', 'quarter_share', 'year_share', 'payout_threshold'):
            if baseline.parameters.get(key) is not None:
                initial[key] = baseline.parameters[key]
    form, error = JobForm(initial=initial), ''
    if request.method == 'POST':
        if not can_start:
            raise PermissionDenied
        posted = request.POST.copy()
        for key, value in {'name': 'Все кварталы 2026', 'variant': ALL_2026, 'fact_year': '2026', 'mode': 'manual', 'enabled': ''}.items():
            posted[key] = value
        form = JobForm(posted)
        if form.is_valid():
            job = form.save()
            try:
                calculation_jobs.start_job(job)
                job.synced = True; job.save(update_fields=['synced'])
                messages.success(request, 'Дополнительный расчёт 2026 запущен. Основные результаты не заменяются.')
                return redirect(reverse('motivation_report_all_2026') + '?run=1')
            except ValidationError as exc:
                error = ' '.join(exc.messages)
            except Exception as exc:
                from uuid import uuid4
                reference = uuid4().hex[:12]
                logging.getLogger(__name__).exception('Additional 2026 run failed [%s]', reference)
                error = calculation_jobs.failure_message(exc, reference)
            job.error = error; job.save(update_fields=['error'])
    job = MotivationCalculationSchedule.objects.filter(variant=ALL_2026).order_by('-created_at', '-pk').first()
    observed = observe(job, persist=can_start) if job else None
    pending = bool(observed and observed.state in ('queued', 'running'))
    execution = MotivationExecution.objects.filter(parameters__variant=ALL_2026, state='completed').order_by('-completed_at', '-pk').first()
    if observed and observed.parameters.get('variant') != ALL_2026:
        observed = None
    if observed and observed.state == 'completed':
        execution = observed
    pending = pending or bool(job and job.synced and not job.error and observed is None and (not execution or execution.job_key != job.key))
    data, presentation = None, {}
    if query.get('run') == '1' or query.get('export') == 'xlsx':
        try:
            if execution is None:
                raise ValidationError('Дополнительный расчёт ещё не завершён. Результаты основного варианта здесь не используются.')
            with reports_next.current_run(execution):
                data = data_for(request.user, execution, query)
            presentation = present('registry', data, data, query.getlist('period') or list('12345'))
            if query.get('search', '').strip():
                raise ValidationError('Для отбора сотрудника используйте поле «Сотрудник».')
            if query.get('export') == 'xlsx':
                from .reports_next_views import workbook
                output = workbook('registry', data, [('Вариант', 'Все кварталы и год за 2026'), ('Премиальная база', 'Весь 2026 год'), ('Запуск', str(execution.run_id)), ('Завершён', str(execution.completed_at)), ('План', execution.parameters.get('plan_version', '')), ('Квартальная доля, %', execution.parameters.get('quarter_share', '')), ('Годовая доля, %', execution.parameters.get('year_share', '')), ('Порог выплаты', execution.parameters.get('payout_threshold', ''))], query=query, title='Все кварталы 2026')
                return FileResponse(output, as_attachment=True, filename='motivation-all-2026.xlsx')
            for section in presentation['team_sections']:
                section['page'] = Paginator(section['rows'], 100).get_page(query.get(section['page_parameter']))
        except PermissionDenied:
            raise
        except ValidationError as exc:
            error = ' '.join(exc.messages)
        except Exception:
            logging.getLogger(__name__).exception('Additional 2026 report failed')
            error = 'Не удалось получить отдельные результаты 2026. Проверьте подключение DataWH и состояние задания.'
    branches = scope['subdivision'] if scope else list(Subdivision.objects.values_list('subdivision_key', flat=True))
    branches = [branches] if isinstance(branches, str) else branches
    kinds = scope['type'] if scope else ['Политики', 'Продажи', 'Директора']
    kinds = [kinds] if isinstance(kinds, str) else kinds
    params = query.copy(); params['run'] = '1'; params['export'] = 'xlsx'
    return render(request, 'admin_motivation/reports_next/all_2026.html', {
        'kind': 'registry', 'all_2026_report': True, 'query': query, 'form': form, 'can_start': can_start, 'error': error,
        'job': job, 'pending': pending, 'execution': execution, 'data': data, 'ledger': data,
        'presentation': presentation, 'periods': [{'code': code, 'label': label, 'selected': not query.getlist('period') or code in query.getlist('period')} for code, label in [('1','I квартал'),('2','II квартал'),('3','III квартал'),('4','IV квартал'),('5','Год')]],
        'branches': branches, 'kinds': kinds, 'page_query': query.urlencode(), 'export_url': '?' + params.urlencode(),
    })
