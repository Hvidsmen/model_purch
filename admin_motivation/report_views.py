import csv
import logging
from time import perf_counter
from django.core.exceptions import ValidationError, PermissionDenied
from django.http import Http404, HttpResponse, FileResponse
from django.shortcuts import render, get_object_or_404
from django.urls import reverse
from .services.reports import CATALOG, FILTERS, PREMIUM_PERIODS, parameters, choices, load_report
from .services.versions import today
from .models import Subdivision, SalesPlanScenario

from base.access import role_for, REPORT_ROLES, BRANCH_DIRECTOR, MOTIVATION_VIEWER, FINANCIER
from base.report_access import politician_scope, scoped_query, PERSONAL_REPORTS, report_kinds

logger = logging.getLogger(__name__)


def report_index(request):
    catalog = {key: CATALOG[key] for key in report_kinds(request.user)} if role_for(request.user, 'motivation') in REPORT_ROLES else CATALOG
    return render(request, 'admin_motivation/reports/index.html', {'reports': catalog, 'subdivisions': Subdivision.objects.none() if role_for(request.user, 'motivation') in REPORT_ROLES else Subdivision.objects.all()})


def report_page(request, kind):
    if kind not in CATALOG: raise Http404('Отчёт не найден.')
    access_scope = getattr(request, 'personal_report_scope', None) or politician_scope(request.user)
    if role_for(request.user, 'motivation') in REPORT_ROLES and (not access_scope or kind not in report_kinds(request.user)):
        raise PermissionDenied
    query = scoped_query(request.GET, access_scope, kind) if access_scope else request.GET.copy()
    if kind == 'deals':
        query.setdefault('start', today().replace(month=1, day=1).isoformat())
        query.setdefault('end', today().isoformat())
    filters = []
    errors = []
    data = None
    values = {}
    try:
        values = parameters(kind, query, trusted_identity=True) if access_scope else parameters(kind, query)
        excel = kind in ('manager-detail', 'subdivisions') and query.get('export') == 'xlsx'
        if excel and query.get('table', 'all') not in ('all', 'summary', 'detail'):
            raise ValidationError('Неизвестная таблица для выгрузки.')
        if not excel:
            options = {'access_scope': access_scope} if access_scope else {}
            filters = choices(kind, values, refresh=request.GET.get('refresh_choices') == '1', **options)
        if request.GET.get('run') == '1' or excel:
            try: page = max(1, int(query.get('page', 1)))
            except ValueError: page = 1
            options = {'access_scope': access_scope} if access_scope else {}
            data = load_report(kind, values, page, 'matrix' if query.get('scope')=='matrix' else 'subdivisions', **options)
            if excel:
                table = query.get('table', 'all')
                if kind == 'subdivisions':
                    from .services.subdivision_excel import subdivision_excel
                    output = subdivision_excel(data, values, table, 'matrix' if query.get('scope') == 'matrix' else 'subdivisions')
                    filename = f'motivation-subdivisions-{table}.xlsx'
                else:
                    from .services.report_excel import report_excel
                    output = report_excel(data, values, table)
                    filename = f'motivation-manager-{data["currency"]}-{table}.xlsx'
                return FileResponse(output, as_attachment=True,
                    filename=filename,
                    content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
            if kind in ('manager-detail', 'subdivisions'):
                data['excel_urls'] = {}
                for table in ('all', 'summary', 'detail'):
                    target = query.copy()
                    target['run'], target['export'], target['table'] = '1', 'xlsx', table
                    target.pop('refresh_choices', None)
                    data['excel_urls'][table] = '?' + target.urlencode()
    except ValidationError as error:
        errors = error.messages
    except RuntimeError:
        errors = ['Не настроено подключение DWH_SQL_CONN_STR к данным отчётов. Настройте его и перезапустите сервер.']
    except Exception:
        logger.exception('Motivation report failed: %s', kind)
        errors = ['Не удалось получить данные отчёта. Проверьте доступ к DataWH и журнал сервера.']
    if not filters:
        filters = [{'name': name, 'label': caption, 'multiple': multiple, 'options': []} for name, _, caption, _, multiple in FILTERS[kind]]
    if access_scope:
        from base.report_access import identity_choices
        fixed = identity_choices(kind, values, access_scope)
        for item in filters:
            if item['name'] in fixed:
                item.update(fixed[item['name']])
    if kind == 'deals' and data:
        columns = [('АвторСделки','Автор сделки','text'), ('Канал','Канал','text'), ('Подразделение','Подразделение','text'),
            ('ДатаЗакрытияСделки','Дата закрытия','date'), ('ДатаОтгрузки','Дата отгрузки','date'), ('Партнер','Партнёр','text'),
            ('Номер','Номер','text'), ('ГруппаПлановПродаж','Группа планов продаж','text'), ('ГруппаТоваров','Группа товаров','text'),
            ('Марка','Марка','text'), ('Модель','Модель','text'), ('ТипСкидки','Тип скидки','text'),
            ('ПроцентСкидки','% скидки','raw_percent'), ('СегментСкидки','Сегмент скидки','text'),
            ('КоэфициентМотивации','Коэффициент мотивации','percent'), ('СуммаОтгрузокUSD','Отгрузки, USD','money'),
            ('СуммаКвМотивацииUSD','Возможная квартальная мотивация, USD','money'), ('СуммаМотивацииUSDГод','Возможная годовая мотивация, USD','money')]
        data['columns'] = [{'title': title, 'type': type_, 'detail': key in {'Канал','Подразделение','ДатаОтгрузки','ГруппаПлановПродаж','ГруппаТоваров','Марка','СегментСкидки'}} for key,title,type_ in columns]
        data['display_rows'] = [{'number': data['offset']+index+1, 'cells': [{'value': row.get(key), 'type': type_, 'detail': data['columns'][i]['detail']} for i,(key,_,type_) in enumerate(columns)]} for index,row in enumerate(data['details'])]
        for page_key, number in [('previous_url', data['page']-1), ('next_url', data['page']+1)]:
            target = query.copy(); target['page']=number; target['run']='1'; target.pop('export',None)
            data[page_key] = '?' + target.urlencode()
        if query.get('export') == 'csv':
            response = HttpResponse(content_type='text/csv; charset=utf-8')
            response['Content-Disposition']='attachment; filename="closed-deals-page.csv"'
            response.write('\ufeff'); writer = csv.writer(response, delimiter=';')
            writer.writerow(['№', *[title for _,title,_ in columns]])
            for row in data['display_rows']:
                values = [cell['value'] for cell in row['cells']]
                values = ["'"+value if isinstance(value,str) and value.lstrip().startswith(('=','+','-','@')) else value for value in values]
                writer.writerow([row['number'],*values])
            return response
    selected_parameters = []
    if kind == 'manager-detail' and data:
        for name, parameter, caption, dataset, multiple in FILTERS[kind]:
            selected = values.get(parameter)
            selected = selected if isinstance(selected, list) else [selected]
            selected_parameters.append({'label': caption, 'value': ', '.join(str(item) for item in selected if item not in (None, '')) or 'Все'})
        selected_parameters.append({'label': 'Валюта мотивации', 'value': data['currency']})
    from .models import MotivationExecution
    from .services.workflow import export_snapshot
    latest_execution=MotivationExecution.objects.order_by('-started_at').first()
    if role_for(request.user, 'motivation') in (MOTIVATION_VIEWER, FINANCIER) and str(query.get('job','')).isdigit():
        from .models import MotivationCalculationSchedule
        from .services.execution_tracking import observe
        job=MotivationCalculationSchedule.objects.filter(pk=query['job']).first()
        if job:
            latest_execution=observe(job,persist=False) or latest_execution
    requested_execution=query.get('execution')
    receipt=None
    if latest_execution:
        receipt={'run':latest_execution,'versions':latest_execution.export_snapshot.get('versions',[]),
                 'stale': latest_execution.export_snapshot.get('fingerprint') != export_snapshot()['fingerprint'],
                 'older_link':bool(requested_execution and str(latest_execution.pk)!=requested_execution),
                 'year_mismatch':bool(query.getlist('year') and str(latest_execution.parameters.get('fact_year')) not in query.getlist('year'))}
    return render(request, 'admin_motivation/reports/report.html', {
        'pilot_periods': bool(access_scope and access_scope.get('pilot_periods') and kind in ('manager', 'manager-detail')),
        'execution_receipt':receipt, 'kind': kind, 'definition': CATALOG[kind], 'filters': filters, 'query': query,
        'premium_period_labels': [dict(PREMIUM_PERIODS)[value] for value in values.get('PremiumPeriods', [])],
        'selected_parameters': selected_parameters, 'errors': errors, 'data': data, 'ran': request.GET.get('run')=='1',
        'subdivisions': Subdivision.objects.none() if access_scope else Subdivision.objects.all(),
        'scope': query.get('scope','subdivisions'),
        'deputy_director_author': access_scope.get('director_author') if access_scope else None,
    })


def plan_summary(request):
    from .services.plan_summary import summary_report, summary_filters
    started = perf_counter()
    access_scope = getattr(request, 'personal_report_scope', None) or politician_scope(request.user)
    if role_for(request.user, 'motivation') in REPORT_ROLES:
        raise PermissionDenied
    query = request.GET.copy()
    options = {}
    if access_scope:
        permitted = access_scope['subdivision']
        selected_branches = [value for value in query.getlist('subdivision') if value in permitted] or permitted
        query.setlist('subdivision', selected_branches)
        options['allowed_subdivisions'] = permitted
    plans = SalesPlanScenario.objects.filter(is_deleted=False).order_by('-pk')
    plan = None
    summary = {}
    selected = request.GET.get('plan', '').strip()
    if selected:
        if not selected.isdecimal():
            raise Http404('План продаж не найден.')
        plan = get_object_or_404(plans, pk=selected)
    else:
        plan = plans.first()
    if plan:
        try:
            summary = summary_report(plan, query, **options)
            summary['summary_filters'] = summary_filters(plan, query, **options)
        except ValidationError as error:
            summary = {'summary_error': ' '.join(error.messages)}
    calculated = perf_counter()
    response = render(request, 'admin_motivation/reports/plan_summary.html', {
        'plans': plans, 'plan': plan, 'subdivisions': Subdivision.objects.none() if access_scope else Subdivision.objects.all(), **summary,
    })
    rendered = perf_counter()
    response['Server-Timing'] = f'calculation;dur={(calculated - started) * 1000:.1f}, render;dur={(rendered - calculated) * 1000:.1f}'
    if rendered - started >= 3:
        logger.warning('Slow plan summary plan=%s calculation=%.3fs render=%.3fs',
                       plan.pk if plan else None, calculated - started, rendered - calculated)
    return response
