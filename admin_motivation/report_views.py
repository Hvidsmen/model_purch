import csv
import logging
from django.core.exceptions import ValidationError
from django.http import Http404, HttpResponse
from django.shortcuts import render, get_object_or_404
from django.urls import reverse
from .services.reports import CATALOG, FILTERS, parameters, choices, load_report
from .services.versions import today
from .models import Subdivision, SalesPlanScenario

logger = logging.getLogger(__name__)


def report_index(request):
    return render(request, 'admin_motivation/reports/index.html', {'reports': CATALOG, 'subdivisions': Subdivision.objects.all()})


def report_page(request, kind):
    if kind not in CATALOG: raise Http404('Отчёт не найден.')
    query = request.GET.copy()
    if kind == 'deals':
        query.setdefault('start', today().replace(month=1, day=1).isoformat())
        query.setdefault('end', today().isoformat())
    filters = []
    errors = []
    data = None
    values = {}
    try:
        values = parameters(kind, query)
        filters = choices(kind, values, refresh=request.GET.get('refresh_choices') == '1')
        if request.GET.get('run') == '1':
            try: page = max(1, int(query.get('page', 1)))
            except ValueError: page = 1
            data = load_report(kind, values, page, 'matrix' if query.get('scope')=='matrix' else 'subdivisions')
    except ValidationError as error:
        errors = error.messages
    except RuntimeError:
        errors = ['Не настроено подключение DWH_SQL_CONN_STR к данным отчётов. Настройте его и перезапустите сервер.']
    except Exception:
        logger.exception('Motivation report failed: %s', kind)
        errors = ['Не удалось получить данные отчёта. Проверьте доступ к DataWH и журнал сервера.']
    if not filters:
        filters = [{'name': name, 'label': caption, 'multiple': multiple, 'options': []} for name, _, caption, _, multiple in FILTERS[kind]]
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
    return render(request, 'admin_motivation/reports/report.html', {
        'kind': kind, 'definition': CATALOG[kind], 'filters': filters, 'query': query,
        'errors': errors, 'data': data, 'ran': request.GET.get('run')=='1',
        'subdivisions': Subdivision.objects.all(),
        'scope': query.get('scope','subdivisions'),
    })


def plan_summary(request):
    from .services.plan_summary import summary_report, summary_filters
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
            summary = summary_report(plan, request.GET)
            summary['summary_filters'] = summary_filters(plan, request.GET)
        except ValidationError as error:
            summary = {'summary_error': ' '.join(error.messages)}
    return render(request, 'admin_motivation/reports/plan_summary.html', {
        'plans': plans, 'plan': plan, 'subdivisions': Subdivision.objects.all(), **summary,
    })
