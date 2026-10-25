"""New report tab; no redirects or replacements of the legacy report pages."""
from tempfile import TemporaryFile
from zoneinfo import ZoneInfo
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.http import FileResponse, Http404
from django.shortcuts import render, get_object_or_404
from django.urls import reverse
from django.views.decorators.http import require_GET
from base.access import role_for, REPORT_ROLES
from .models import MotivationExecution, SalesPlanScenario, Subdivision
from .services import reports_next as reports


@require_GET
def index(request):
    return render(request, 'admin_motivation/reports_next/index.html', {'catalog': reports.available(request.user)})


def workbook(kind, data, metadata):
    from openpyxl import Workbook
    from openpyxl.cell import WriteOnlyCell
    from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
    book = Workbook(write_only=True)
    sheet = book.create_sheet('Премии')
    sheet.freeze_panes = 'B3'
    def append(values):
        cells = []
        for position, value in enumerate(values):
            cell = WriteOnlyCell(sheet, value=ILLEGAL_CHARACTERS_RE.sub('', value)[:32767] if isinstance(value, str) else value)
            if isinstance(value, str):
                cell.data_type = 's'
            elif value is not None:
                cell.number_format = '0.00%' if kind == 'executive' and position == 4 else '#,##0.00'
            cells.append(cell)
        sheet.append(cells)
    append([reports.CATALOG[kind][0]])
    if kind == 'executive':
        append(['Подразделение / вид мотивации / директор', 'План: глобальные коэффициенты, USD', 'План: коэффициенты подразделений, USD', 'Разница, USD', 'Разница, %'])
        def write(nodes, indent=0):
            for node in nodes:
                append(['  ' * indent + node['name'], node['global'], node['local'], node['difference'], node.get('difference_percent')])
                write(node.get('children', []), indent + 1)
        write(data)
    else:
        append(['Подразделение', 'Автор сделки', 'Вид мотивации', 'Период', 'Валюта', 'Рассчитано', 'Доплата', 'Всего', 'Статус'])
        for row in data['rows']:
            append([row['subdivision'], row['author'], row['kind'], row['period'], row['currency'], row['amount'], row['bonus'], row['total'], 'Не распределено' if row['unallocated'] else 'Рассчитано; выплата не подтверждена'])
    notes = book.create_sheet('Параметры')
    for label, value in metadata:
        cells = [WriteOnlyCell(notes, value=ILLEGAL_CHARACTERS_RE.sub('', str(item))[:32767]) for item in (label, value)]
        for cell in cells:
            cell.data_type = 's'
        notes.append(cells)
    # Explicit strings also protect parameter values from Excel formula evaluation.
    # WriteOnlyCell is required because write-only sheets do not expose cells.
    output = TemporaryFile(suffix='.xlsx')
    try:
        book.save(output)
        output.seek(0)
        return output
    except Exception:
        output.close()
        raise
    finally:
        book.close()


@require_GET
def report(request, kind):
    if kind not in reports.CATALOG:
        raise Http404
    if kind not in reports.available(request.user):
        raise PermissionDenied
    query = request.GET.copy()
    try:
        scope, types, period = reports.restrict(request.user, query)
    except ValidationError as exc:
        from django.http import HttpResponseBadRequest
        return HttpResponseBadRequest(' '.join(exc.messages))
    if kind == 'detail' and not query.get('type'):
        query['type'] = types[0]
    runs = MotivationExecution.objects.filter(state='completed').order_by('-started_at', '-pk')
    selected = get_object_or_404(runs, pk=query['execution']) if query.get('execution', '').isdigit() else runs.first()
    plans = SalesPlanScenario.objects.filter(is_deleted=False, loaded_at__isnull=False).order_by('-loaded_at', '-pk')
    original_plan = reports.default_plan(selected) if selected else None
    plan = get_object_or_404(plans, pk=query['plan']) if query.get('plan', '').isdigit() else original_plan
    data, error, detail = None, '', None
    if selected:
        query.setdefault('year', str(selected.parameters.get('fact_year', '')))
    query.setdefault('currency', 'USD')
    query.setdefault('period', 'all')
    metadata = [('Расчёт', selected.completed_at.astimezone(ZoneInfo('Europe/Moscow')).strftime('%d.%m.%Y %H:%M МСК') if selected and selected.completed_at else 'Нет'), ('Год', query.get('year', '')),
        ('План расчёта DWH', selected.parameters.get('plan_version', '') if selected else ''),
        ('План сравнения' if kind == 'executive' else 'Загруженная копия плана расчёта', str(plan) if plan else 'Не загружена'), ('Период премии', dict(reports.PERIODS).get(query['period'], '')),
        ('Валюта', 'USD' if kind == 'executive' else query['currency']),
        ('Порог выполнения для выплаты', selected.parameters.get('payout_threshold', 'Не сохранён') if selected else ''),
        ('Статус выплат', 'Данных о согласовании и фактической оплате нет. Показаны рассчитанные суммы.')]
    try:
        if query.get('run') == '1' or query.get('export') == 'xlsx':
            if not selected:
                raise ValidationError('Нет подтверждённых завершённых расчётов.')
            if kind == 'executive':
                if not plan:
                    raise ValidationError('План из расчёта не найден среди загруженных до запуска. Выберите загруженный план сравнения.')
                if query['year'] != str(selected.parameters.get('fact_year')):
                    raise ValidationError('Год должен совпадать с годом выбранного расчёта.')
                data = reports.planned_budget(selected, plan, query)
            else:
                with reports.current_run(selected):
                    data = reports.actual_reports(request.user, selected, query, detailed=kind == 'detail')
                for row in data['rows']:
                    target = query.copy()
                    target['type'], target['period'], target['run'] = row['kind'], row['code'], '1'
                    target.setlist('subdivision', [row['subdivision']])
                    target.setlist('author', [row['author']])
                    row['detail_url'] = reverse('motivation_report_next', args=['detail']) + '?' + target.urlencode()
                if kind == 'detail':
                    detail = data['sections'][0]['data'] if data['sections'] else None
            if query.get('export') == 'xlsx':
                if kind == 'detail' and detail:
                    from .services.report_excel import report_excel
                    output = report_excel(detail, {'Currency': query['currency'], 'Years': [query['year']], 'TypeCoeff_': query.get('type'), 'Subdivision': query.getlist('subdivision'), 'DealAuthor': query.getlist('author')}, query.get('table', 'all'), provenance=metadata)
                else:
                    output = workbook(kind, data, metadata)
                return FileResponse(output, as_attachment=True, filename=f'motivation-next-{kind}.xlsx', content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
            if detail:
                detail['excel_urls'] = {}
                for table in ('all', 'summary', 'detail'):
                    target = query.copy()
                    target['export'], target['table'] = 'xlsx', table
                    detail['excel_urls'][table] = '?' + target.urlencode()
    except ValidationError as exc:
        error = ' '.join(exc.messages)
    except PermissionDenied:
        raise
    except Exception:
        import logging
        logging.getLogger(__name__).exception('New motivation report failed: %s', kind)
        error = 'Не удалось получить данные. Проверьте подключение к DWH и журнал сервера.'
    selected_subs = query.getlist('subdivision')
    branch_names = scope.get('subdivision', []) if scope else list(Subdivision.objects.values_list('subdivision_key', flat=True))
    branch_names = [branch_names] if isinstance(branch_names, str) else branch_names
    branches = [{'name': value, 'selected': value in selected_subs} for value in branch_names]
    period_options = [item for item in reports.PERIODS if not scope or not scope.get('pilot_periods') or item[0] in ('all', '4', '5')]
    export_query = query.copy()
    export_query['export'] = 'xlsx'
    ledger_page = Paginator(data['rows'], 100).get_page(query.get('page')) if data is not None and kind not in ('executive', 'detail') else None
    page_query = query.copy()
    page_query.pop('page', None)
    page_query.pop('export', None)
    all_types = scope.get('type', []) if scope else ['Политики', 'Продажи', 'Директора']
    all_types = [all_types] if isinstance(all_types, str) else all_types
    return render(request, 'admin_motivation/reports_next/report.html', {'kind': kind, 'title': reports.CATALOG[kind][0],
        'catalog': reports.available(request.user), 'data': detail if kind == 'detail' else data, 'ledger': data,
        'current_execution_id': runs.first().pk if runs.exists() else None, 'runs': runs, 'execution': selected, 'plans': plans, 'plan': plan, 'query': query, 'types': types,
        'all_types': all_types,
        'periods': period_options, 'branches': branches, 'metadata': metadata, 'error': error, 'export_url': '?' + export_query.urlencode(),
        'ledger_page': ledger_page, 'page_query': page_query.urlencode(), 'selected_parameters': [], 'calculation_steps': [{'period': label, 'possible': block[13], 'ratio': block[16], 'calculated': block[17], 'bonus': block[18], 'total': block[19]} for label, block in zip(detail['periods'], detail['detail_totals'])] if detail else [],
        'is_personal': role_for(request.user, 'motivation') in REPORT_ROLES})
