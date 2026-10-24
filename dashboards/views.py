import csv
import logging
from datetime import date
from zoneinfo import ZoneInfo
from django.core.exceptions import ValidationError, PermissionDenied
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render, redirect
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.http import require_GET, require_http_methods
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
    for field, default in [('s_max', 10), ('m_max', 50), ('l_max', 250)]:
        try:
            result[field] = int(query.get(field, default))
        except (TypeError, ValueError):
            raise ValidationError('Границы размеров должны быть целыми числами.')
    if not 0 < result['s_max'] < result['m_max'] < result['l_max'] <= 1_000_000_000:
        raise ValidationError('Нужно 0 < граница S < граница M < граница L; XL — всё выше L.')
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
    snapshot_date = date.fromisoformat(source['as_of']) if source.get('as_of') else as_of
    loaded_time = parse_datetime(source['loaded_at'])
    if loaded_time and timezone.is_naive(loaded_time):
        loaded_time = timezone.make_aware(loaded_time, ZoneInfo('UTC'))
    stale = bool(loaded_time and (timezone.now() - loaded_time).total_seconds() > 24 * 60 * 60)
    report = analyze(source['rows'], values, snapshot_date)
    report['loaded_at'] = source['loaded_at']
    report['source_end'] = source['end_exclusive']
    report['row_count'] = len(source['rows'])
    report['snapshot_warning'] = ('Формула источника изменилась. Данные показаны из предыдущего снимка; требуется обновление.' if source.get('source_changed') else
        'Обновление сейчас выполняется. Показан предыдущий успешный снимок.' if source.get('refresh_status', {}).get('state') == 'running' else
        'Последнее обновление завершилось ошибкой. Показан последний успешный снимок.' if source.get('refresh_status', {}).get('state') in ('error', 'interrupted') else
        'Снимок старше суток. Проверьте задание обновления данных.' if stale else '')
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
    writer.writerow(['Границы S / M / L (шт.)', *report['thresholds']])
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


@require_GET
def refresh_status(request):
    from .services.snapshots import status, read_json
    current = status()
    history = read_json('history.json', [])
    if current['run'].get('state') in ('running', 'interrupted') and not any(row.get('started_at') == current['run'].get('started_at') for row in history):
        history = [current['run'], *history][:30]
    return JsonResponse({**current, 'history': history})


@require_http_methods(['GET', 'POST'])
def refresh_data(request):
    from .services.snapshots import status, read_json, save_schedule, start_refresh
    from base.access import can_access
    from django.contrib import messages
    if request.method == 'POST':
        if not can_access(request.user, 'dashboards_admin'):
            if 'application/json' in request.headers.get('Accept', ''):
                return JsonResponse({'error': 'Запуск и расписание доступны только администратору проекта «Дашборды».'}, status=403)
            raise PermissionDenied
        action = request.POST.get('action')
        if action == 'run':
            started = start_refresh()
            if 'application/json' in request.headers.get('Accept', ''):
                return JsonResponse({'started': started, **status()}, status=202)
            messages.info(request, 'Обновление запущено.' if started else 'Обновление уже выполняется.')
        elif action == 'schedule':
            try:
                save_schedule(request.POST.get('enabled') == 'on', request.POST.get('time', ''))
                messages.success(request, 'Расписание сохранено. Время — московское.')
            except ValidationError as error:
                messages.error(request, ' '.join(error.messages))
        else:
            return JsonResponse({'error': 'Неизвестное действие.'}, status=400)
        return redirect('dashboard_refresh')
    return render(request, 'dashboards/refresh.html', {**status(), 'history': read_json('history.json', [])})
