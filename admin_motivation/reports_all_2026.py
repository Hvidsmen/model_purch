"""Independent 2026 calculation and scoped report; main result tables stay intact."""
from .services.transition_2026 import RULE_VERSION
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
from .services.all_2026_access import can_view


def selected_variant(query):
    value = query.get('scenario', 'combined')
    if value not in ('combined', 'separate'):
        raise ValidationError('Выберите объединённый или раздельный сценарий.')
    return 'separate_2026' if value == 'separate' else ALL_2026


def selection(user, query):
    if not can_view(user):
        raise PermissionDenied
    scope = politician_scope(user)
    if role_for(user, 'motivation') in REPORT_ROLES and not scope:
        raise PermissionDenied
    allowed = scope['type'] if scope else ['Политики', 'Продажи', 'Директора']
    allowed = [allowed] if isinstance(allowed, str) else allowed
    kinds = reports_next.selected_types(query, allowed)
    variant = selected_variant(query)
    available_periods = '123' if variant == 'separate_2026' else '12345'
    periods = list(dict.fromkeys(query.getlist('period'))) or list(available_periods)
    if any(code not in available_periods for code in periods) or query.get('year', '2026') != '2026':
        raise ValidationError('Дополнительный вариант: только 2026 год и кварталы I–IV/год.')
    if query.get('currency', 'USD') not in ('USD', 'RUB'):
        raise ValidationError('Выберите USD или RUB.')
    if scope:
        allowed_branches = scope['subdivision'] if isinstance(scope['subdivision'], list) else [scope['subdivision']]
        from .services.subdivision_aliases import mapping, canonical
        aliases = mapping()
        if any(canonical(branch, aliases) not in allowed_branches for branch in query.getlist('subdivision')):
            raise PermissionDenied
    for field in ('planning','goods','brand','author','subdivision'):
        if len(query.getlist(field)) > 100 or any(len(value)>255 for value in query.getlist(field)):
            raise ValidationError('Слишком много значений фильтра или слишком длинное значение.')
    return scope, kinds, periods


def data_for(user, execution, query, *, detailed=False):
    scope, kinds, periods = selection(user, query)
    variant = selected_variant(query)
    if scope and variant == 'separate_2026':
        from .services.subdivision_aliases import expand, mapping
        scope = {**scope, 'subdivision': expand(scope['subdivision'], mapping())}
    rows, plan_fact, department, sections = [], [], [], []
    from .services.annual_accrual import SNAPSHOT_VERSION
    snapshot_options = ({'annual_accrual_snapshot': True}
                        if execution.parameters.get('annual_accrual_source') == SNAPSHOT_VERSION else {})
    for kind in kinds:
        values = {'TypeCoeff_': kind, 'Subdivision': query.getlist('subdivision'), 'DealAuthor': query.getlist('author'),
                  'gp_sales': query.getlist('planning'), 'GPGoods': query.getlist('goods'), 'Brend': query.getlist('brand'), 'Years': ['2026'], 'Currency': query.get('currency', 'USD')}
        if scope:
            values = scoped_values('manager-detail', values, scope)
        data = reports_next.load_report('manager-detail', values, access_scope=scope, premium_period=periods,
                                        summary_only=not detailed, calculation_variant=variant,
                                        **({'include_performance':True} if detailed else {}), **snapshot_options)
        if detailed:sections.append({'kind':kind,'data':data})
        from .services.motivation_performance import presentation_rows, DEPARTMENT
        plan_fact.extend(presentation_rows(data.get('motivation_performance', []), kind))
        department.extend(presentation_rows(data.get('department_performance', []), DEPARTMENT))
        summary = data['period_summary']
        from .services.annual_accrual import lookup
        annual = lookup(data.get('annual_accrual', []))
        for item in summary['rows']:
            for index, column in enumerate(summary['columns']):
                amount = item['values'][index]
                if amount is None:
                    continue
                code = reports_next.period_code(column['label'])
                target = query.copy()
                target['detail']='1';target['run']='1';target.pop('export',None)
                target.setlist('period', [reports_next.period_code(column['label'])])
                target.setlist('type', [kind]); target.setlist('author', [item['author']]); target.setlist('subdivision', [item['subdivision']])
                rows.append({'subdivision': item['subdivision'], 'author': item['author'], 'unallocated': item.get('unallocated', False),
                    'kind': kind, 'period': column['label'], 'code': reports_next.period_code(column['label']),
                    'amount': amount, 'bonus': Decimal(0), 'total': amount, 'currency': values['Currency'],
                    'possible': item.get('possible_values', [None] * len(summary['columns']))[index],
                    'annual_accrual': annual.get((item['subdivision'], item['author'], code),
                        Decimal(0) if snapshot_options and code in ('1','2','3','4') else None),
                    'detail_url': reverse('motivation_report_all_2026') + '?' + target.urlencode()})
    return {'rows': rows, 'sections':sections, 'plan_fact': plan_fact, 'department_plan_fact': department, 'kinds': kinds, 'has_unallocated': any(row['unallocated'] for row in rows),
            'total': sum((row['amount'] for row in rows), Decimal(0)), 'currency': query.get('currency', 'USD')}


@require_http_methods(['GET', 'POST'])
def report(request):
    if not can_view(request.user):
        raise PermissionDenied
    from .operation_views import JobForm
    from .services import calculation_jobs
    from .services.execution_tracking import observe, observe_latest
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
    # The combined scenario has one canonical subdivision, while the separate
    # scenario retains each selected original. Validate access before mapping.
    selected = query.getlist('subdivision')
    if (selected_variant(query) == ALL_2026 and selected
            and all(value.strip().casefold() in ('д-москва','термотрейд') for value in selected)):
        query.setlist('subdivision', ['Д-Москва'])
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
            # Repeated clicks with identical parameters must reuse the Agent job,
            # including after a failed/blocked start. Keep existing run history.
            fields = ('plan_version', 'quarter_share', 'year_share', 'payout_threshold')
            existing = MotivationCalculationSchedule.objects.filter(
                variant=ALL_2026, fact_year=2026, mode='manual',
                **{field: form.cleaned_data[field] for field in fields}
            ).order_by('-synced', 'pk').first()
            job = existing or form.save()
            try:
                if existing and calculation_jobs.monitor(job).get('status') in ('Выполняется', 'Ожидает запуска'):
                    messages.info(request, 'Дополнительный расчёт с этими параметрами уже выполняется. Дождитесь завершения; новое задание не создаётся.')
                    return redirect(reverse('motivation_report_all_2026') + '?run=1')
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
    variant = selected_variant(query)
    observed = observe_latest(variant, persist=can_start)
    if observed is None and job and variant == ALL_2026:
        observed = observe(job, persist=can_start)
    pending = bool(observed and observed.state in ('queued', 'running'))
    execution = MotivationExecution.objects.filter(parameters__variant=variant, state='completed').order_by('-completed_at', '-pk').first()
    if observed and observed.parameters.get('variant') != variant:
        observed = None
    if observed and observed.state == 'completed':
        execution = observed
    pending = pending or bool(job and job.synced and not job.error and observed is None and (not execution or execution.job_key != job.key))
    data, presentation, detail = None, {}, None
    is_detail=query.get('detail')=='1'
    if query.get('run') == '1' or query.get('export') == 'xlsx':
        try:
            if observed and observed.state != 'completed':
                raise ValidationError('Выбранный сценарий ещё не завершён успешно. Дождитесь расчёта.')
            if execution is None:
                raise ValidationError('Дополнительный расчёт ещё не завершён. Результаты основного варианта здесь не используются.')
            with reports_next.current_run(execution):
                data = data_for(request.user, execution, query, detailed=is_detail)
            presentation = present('registry', data, data, query.getlist('period') or list('123' if variant == 'separate_2026' else '12345'))
            from .services.reports_next_presenter import subdivision_summaries
            presentation['subdivision_summaries']=subdivision_summaries(data,presentation['period_columns'])
            if query.get('search', '').strip():
                raise ValidationError('Для отбора сотрудника используйте поле «Сотрудник».')
            if is_detail and data['sections']:
                from .services.manager_detail import combine_reports
                from .services.reports_next_presenter import new_detail_labels
                detail=new_detail_labels(combine_reports(data['sections'],data['currency']))
                detail['excel_urls']={}
                for table in ('all','summary','detail'):
                    target=query.copy();target['export']='xlsx';target['table']=table
                    detail['excel_urls'][table]='?'+target.urlencode()
                if query.get('export')=='xlsx':
                    from .services.report_excel import report_excel
                    table=query.get('table','all')
                    if table not in ('all','summary','detail'):raise ValidationError('Неизвестная таблица для выгрузки.')
                    output=report_excel(detail,{'Currency':data['currency'],'Years':['2026'],
                        'TypeCoeff_':query.getlist('type'),'Subdivision':query.getlist('subdivision'),
                        'DealAuthor':query.getlist('author'),'gp_sales':query.getlist('planning'),
                        'GPGoods':query.getlist('goods'),'Brend':query.getlist('brand')},table,
                        provenance=[('Вариант','Москва и ТТ раздельно' if variant=='separate_2026' else 'Москва + ТТ объединённо'),
                            ('Запуск',str(execution.run_id)),('Завершён',str(execution.completed_at)),
                            ('План',execution.parameters.get('plan_version','')),
                            ('Квартальная доля, %',execution.parameters.get('quarter_share','')),
                            ('Годовая доля, %',execution.parameters.get('year_share','')),
                            ('Порог выплаты',execution.parameters.get('payout_threshold','')),('Периоды',', '.join(query.getlist('period')) or 'Все доступные')])
                    return FileResponse(output,as_attachment=True,filename='motivation-all-2026-detail.xlsx')
            if query.get('export') == 'xlsx':
                from .reports_next_views import workbook
                output = workbook('registry', data, [('Вариант', 'Москва и ТТ раздельно' if variant == 'separate_2026' else 'Москва + ТТ объединённо'), ('Подразделения', ', '.join(query.getlist('subdivision')) or 'Все доступные'), ('Премиальная база', 'I–III кварталы: выбранный сценарий; IV квартал и годовая часть: только IV квартал'), ('Товарные фильтры', '; '.join(field + ': ' + ', '.join(query.getlist(field)) for field in ('planning','goods','brand') if query.getlist(field))), ('Запуск', str(execution.run_id)), ('Завершён', str(execution.completed_at)), ('План', execution.parameters.get('plan_version', '')), ('Квартальная доля, %', execution.parameters.get('quarter_share', '')), ('Годовая доля, %', execution.parameters.get('year_share', '')), ('Порог выплаты', execution.parameters.get('payout_threshold', ''))], query=query, title='Все кварталы 2026')
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
    from .services.product_filters import fields as product_fields
    from .services.annual_accrual import SNAPSHOT_VERSION
    branches = scope['subdivision'] if scope else list(Subdivision.all_objects.values_list('subdivision_key', flat=True))
    branches = [branches] if isinstance(branches, str) else branches
    if variant == 'separate_2026':
        from .services.subdivision_aliases import expand, mapping
        branches = [name for name in expand(branches, mapping()) if name.strip().casefold() in ('д-москва','термотрейд')]
    else:
        branches = [name for name in branches if name.strip().casefold() == 'д-москва']
    kinds = scope['type'] if scope else ['Политики', 'Продажи', 'Директора']
    kinds = [kinds] if isinstance(kinds, str) else kinds
    params = query.copy(); params['run'] = '1'; params['export'] = 'xlsx'
    target=query.copy();target['run']='1';target['detail']='1';target.pop('export',None)
    detail_url='?'+target.urlencode()
    target.pop('detail',None);target.pop('table',None)
    summary_url='?'+target.urlencode()
    return render(request, 'admin_motivation/reports_next/all_2026.html', {
        'selected_parameters':[{'label':label,'value':', '.join(query.getlist(field)) or 'Все доступные'} for field,label in [('subdivision','Подразделения'),('type','Вид мотивации'),('author','Сотрудник'),('period','Периоды')]],
        'detail':detail,'is_detail':is_detail,'detail_url':detail_url,'summary_url':summary_url,
        'kind': 'registry', 'all_2026_report': True, 'query': query, 'form': form, 'can_start': can_start, 'error': error,
        'job': job, 'pending': pending, 'execution': execution, 'data': data, 'ledger': data,
        'annual_accrual_requires_recalculation': bool(execution and execution.parameters.get('annual_accrual_source') != SNAPSHOT_VERSION),
        'presentation': presentation, 'periods': [{'code': code, 'label': label, 'selected': not query.getlist('period') or code in query.getlist('period')} for code, label in [('1','I квартал'),('2','II квартал'),('3','III квартал'),('4','IV квартал'),('5','Год')] if variant != 'separate_2026' or code in '123'],
        'rule_outdated': bool(execution and execution.parameters.get('transition_rule') != RULE_VERSION), 'scenario': query.get('scenario','combined'), 'product_filter_fields': product_fields(query), 'branches': branches, 'selected_subdivisions': query.getlist('subdivision'), 'kinds': kinds, 'selected_types': query.getlist('type'), 'page_query': query.urlencode(), 'export_url': '?' + params.urlencode(),
    })

