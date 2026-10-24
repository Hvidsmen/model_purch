import csv
import logging
from zoneinfo import ZoneInfo
from django.core.exceptions import ValidationError
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_GET
from .services.dealers import analyze
from .services.source import load_sales

logger = logging.getLogger(__name__)


def options(query, as_of):
    result = {}
    for field, allowed, default in [('method', ('threshold', 'abc'), 'threshold'),
                                    ('scope', ('company', 'branch'), 'company'),
                                    ('measure', ('quantity', 'amount'), 'quantity'),
                                    ('period_mode', ('ltm', 'calendar'), 'ltm')]:
        value = query.get(field, default)
        if value not in allowed:
            raise ValidationError('Некорректный параметр: ' + field)
        result[field] = value
    for field in ('branch', 'brand', 'equipment'):
        values = query.getlist(field)
        if len(values) > 100 or any(len(value) > 255 for value in values):
            raise ValidationError('Слишком много значений фильтра.')
        result[field] = list(dict.fromkeys(values))
    for field in ('start', 'end'):
        value = query.get(field)
        try:
            result[field] = int(value) if value else None
        except ValueError:
            raise ValidationError('Выберите корректный год.')
        if result[field] is not None and not 2021 <= result[field] <= as_of.year:
            raise ValidationError('Год за пределами доступной истории.')
    for field in ('dealer', 'search', 'cohort'):
        result[field] = query.get(field, '')
        if len(result[field]) > 255:
            raise ValidationError('Слишком длинное значение фильтра.')
    result['collapse_low'] = query.get('collapse_low') == '1'
    return result


@require_GET
def index(request):
    return render(request, 'dashboards/index.html')


@require_GET
def dealer_dashboard(request):
    return render(request, 'dashboards/dealers.html')


def report_data(query):
    as_of = timezone.now().astimezone(ZoneInfo('Europe/Moscow')).date()
    values = options(query, as_of)
    source = load_sales(as_of)
    report = analyze(source['rows'], values, as_of)
    report['loaded_at'] = source['loaded_at']
    report['source_end'] = source['end_exclusive']
    report['row_count'] = len(source['rows'])
    report['amount_label'] = 'Выручка без IT, USD'
    return report


@require_GET
def dealer_data(request):
    try:
        return JsonResponse(report_data(request.GET), json_dumps_params={'ensure_ascii': False})
    except ValidationError as error:
        return JsonResponse({'error': ' '.join(error.messages)}, status=400)
    except RuntimeError:
        logger.exception('Dealer dashboard source unavailable')
        return JsonResponse({'error': 'Не настроено подключение к DataWH. Проверьте DWH_SQL_CONN_STR и перезапустите портал.'}, status=503)
    except Exception:
        logger.exception('Dealer dashboard query failed')
        return JsonResponse({'error': 'Не удалось прочитать дилерские продажи. Проверьте подключение к DataWH и поля запроса; неполные данные не показаны.'}, status=503)


def safe_cell(value):
    text = str(value)
    return "'" + text if text.lstrip().startswith(('=', '+', '-', '@')) else text


@require_GET
def dealer_export(request):
    try:
        report = report_data(request.GET)
    except ValidationError as error:
        return JsonResponse({'error': ' '.join(error.messages)}, status=400)
    except Exception:
        logger.exception('Dealer dashboard export failed')
        return JsonResponse({'error': 'Не удалось выгрузить данные. Повторите после загрузки дашборда.'}, status=503)
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = 'attachment; filename="dealer-base-dynamics.csv"'
    response.write('\ufeff')
    writer = csv.writer(response, delimiter=';')
    writer.writerow(['Период', 'Начало', 'Конец (не включая)', 'Группа', 'Дилеров', 'Значение', 'Мера', 'Масштаб'])
    for period in report['overview']:
        for group in period['groups']:
            writer.writerow([period['label'], period['start'], period['end'], group['group'], group['count'], group['value'], report['measure'], report['scope']])
    writer.writerow([])
    writer.writerow(['Переход из', 'Переход в', 'Дилеров', 'Штуки последнего периода', report['amount_label']])
    for row in report['flows']:
        writer.writerow([safe_cell(row['source']), safe_cell(row['target']), row['count'], row['quantity'], row['amount']])
    writer.writerow([])
    writer.writerow(['Мост', 'Изменение', 'Дилеров'])
    for row in report['bridge']['items']:
        writer.writerow([safe_cell(row['label']), row['value'], row['count']])
    writer.writerow([])
    writer.writerow(['Траектория', 'Дилеров', *report['trajectory_periods']])
    for row in report['trajectories']:
        writer.writerow([' → '.join(row['path']), row['count'], *row['values']])
    writer.writerow([])
    writer.writerow(['Когорта', 'Дилеров на старте', 'Год', 'Доля в той же или выше группе', 'Штуки'])
    for row in report['cohorts']:
        for period in row['periods']:
            writer.writerow([row['group'], row['size'], period['year'], period['retained'], period['quantity']])
    writer.writerow([])
    writer.writerow(['Филиал', 'Чистый объём', 'Доля топ-5', 'Группа', 'Доля', 'Изменение доли'])
    for row in report['branches']:
        for group in row['groups']:
            writer.writerow([safe_cell(row['branch']), row['total'], row['top5_share'], group['group'], group['share'], group['delta']])
    writer.writerow([])
    writer.writerow(['Группа последнего периода', 'Происхождение', 'Дилеров'])
    for row in report['origins']:
        for origin, count in row['origins'].items():
            writer.writerow([row['group'], origin, count])
    writer.writerow([])
    writer.writerow(['Год прихода', 'Пришло', 'Активны в последнем периоде', 'В верхней группе'])
    for row in report['arrivals']:
        if row['year'] <= report['end']:
            writer.writerow([row['year'], row['size'], row['alive'], row['top']])
    if report['dealer']:
        writer.writerow([])
        writer.writerow(['Дилер', safe_cell(report['dealer']['code']), safe_cell(' / '.join(report['dealer']['names']))])
        writer.writerow(['Период', 'Штуки', report['amount_label'], 'Группы'])
        for period in report['dealer']['periods']:
            writer.writerow([period['label'], period['quantity'], period['amount'], safe_cell('; '.join(group['branch']+': '+group['group'] for group in period['groups']))])
    return response
