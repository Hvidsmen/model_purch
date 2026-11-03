"""Presentation workbook for team and registry reports; values are not recalculated."""
from decimal import Decimal
from tempfile import TemporaryFile
from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from .reports_next_presenter import present

NAVY = '243650'
PURPLE = '6850C8'
PALE = 'F1EDFC'
GREY = '64748B'
NUMBER = '#,##0.00;[Red](#,##0.00);0.00'
ZERO = Decimal(0)


def workbook(kind, data, metadata, query=None, title=None):
    selected = query.getlist('period') or ['all'] if query is not None else 'all'
    display = present(kind, data, data, selected)
    currency = data['currency']
    heading = title or ('Премии команды' if kind == 'team' else 'Реестр рассчитанных премий')
    book = Workbook(write_only=True)

    def text(value):
        return ILLEGAL_CHARACTERS_RE.sub('', value)[:32767] if isinstance(value, str) else value

    def row(sheet, values, style='body', percent_columns=()):
        dark = style in ('title', 'header', 'grand')
        fill = NAVY if style in ('header', 'grand') else PALE if style == 'subtotal' else 'F7F8FC' if style == 'alternate' else None
        cells = []
        for index, value in enumerate(values):
            cell = WriteOnlyCell(sheet, value=text(value))
            if isinstance(value, str):
                cell.data_type = 's'
            cell.font = Font(name='Calibri', size=20 if style == 'title' else 11,
                             bold=style in ('title', 'header', 'subtotal', 'grand'),
                             color=NAVY if style == 'title' else 'FFFFFF' if dark else GREY if style == 'note' else NAVY)
            if fill:
                cell.fill = PatternFill('solid', fgColor=fill)
            cell.alignment = Alignment(horizontal='right' if isinstance(value, (int, float, Decimal)) else 'left',
                                       vertical='center', wrap_text=style != 'title')
            if isinstance(value, (int, float, Decimal)):
                cell.number_format = '0' if sheet.title == 'Сводка' and index == 1 else '0.00%' if index in percent_columns else NUMBER
            if style not in ('title', 'note', 'blank'):
                cell.border = Border(bottom=Side(style='hair', color='DCE2EE'))
            cells.append(cell)
        current = getattr(sheet, '_portal_row', 0) + 1
        sheet._portal_row = current
        sheet.row_dimensions[current].height = 40 if style in ('title', 'header') else 30 if style == 'note' else 30
        sheet.append(cells)

    def sheet(name, headers, widths, subtitle):
        ws = book.create_sheet(name)
        ws.sheet_view.showGridLines = False
        ws.sheet_view.zoomScale = 90
        ws.freeze_panes = 'C6'
        for index, width in enumerate(widths, 1):
            ws.column_dimensions[get_column_letter(index)].width = width
        ws.sheet_properties.pageSetUpPr.fitToPage = True
        ws.page_setup.orientation = 'landscape'
        ws.page_setup.paperSize = '8'  # ISO A3, supported by write-only worksheets
        ws.page_setup.fitToWidth = 1
        ws.page_setup.fitToHeight = 0
        ws.print_title_rows = '1:5'
        ws.oddFooter.center.text = 'Страница &P из &N'
        ws.oddFooter.right.text = 'DA портал · Мотивация'
        ws.sheet_properties.outlinePr.summaryRight = False
        row(ws, [heading], 'title')
        row(ws, [subtitle], 'note')
        row(ws, ['Валюта: ' + currency + ' · Рассчитанные суммы; выплата не подтверждена'], 'note')
        row(ws, [], 'blank')
        row(ws, headers, 'header')
        return ws

    period_headers = [column['label'] for column in display['period_columns']]
    headers = ['Вид мотивации', 'Сотрудников', *period_headers, 'Годовая доплата', 'Всего по расчёту', 'До условий выполнения']
    ws = sheet('Сводка', headers, [28, 16, *([21] * len(period_headers)), 22, 24, 27],
               'Итоги по выбранным подразделениям и периодам')
    for section in display['team_sections']:
        row(ws, [section['kind'], section['people'], *section['period_totals'],
                 section['bonus'], section['total'], section['possible']], 'subtotal')
    row(ws, ['ИТОГО', display['people'], *display['period_totals'], display['bonus'], display['total'], display['possible']], 'grand')
    row(ws, [], 'blank')
    row(ws, ['«До условий выполнения» — мотивация на фактических отгрузках до применения процента выполнения.'], 'note')
    row(ws, ['Подробности по сотрудникам — на отдельных листах. Состав директоров соответствует текущему справочнику.'], 'note')

    for section in display['team_sections']:
        ws = sheet(section['kind'][:31], ['Подразделение', 'Сотрудник', *period_headers,
                   'Премия по расчёту', 'Годовая доплата', 'Всего по расчёту', 'До условий выполнения', 'Статус'],
                   [26, 44, *([21] * len(period_headers)), 23, 22, 24, 27, 36],
                   section['kind'] + ' · Итоги по всем страницам отчёта')
        for index, employee in enumerate(section['rows']):
            row(ws, [employee['subdivision'], employee['author'], *employee['period_values'],
                     employee['amount'], employee['bonus'], employee['total'], employee['possible'],
                     'Не распределено' if employee['unallocated'] else 'Рассчитано; выплата не подтверждена'],
                'alternate' if index % 2 else 'body')
        last = ws._portal_row
        ws.auto_filter.ref = 'A5:' + get_column_letter(len(period_headers) + 8) + str(max(5, last))
        row(ws, ['ИТОГО · ' + section['kind'], None, *section['period_totals'],
                 section['amount'], section['bonus'], section['total'], section['possible'], None], 'grand')

    ws = sheet('По периодам', ['Подразделение', 'Сотрудник', 'Вид мотивации', 'Период',
               'Валюта', 'До условий выполнения', 'Премия по расчёту', 'Годовая доплата', 'Всего по расчёту', 'Пояснение'],
               [26, 44, 22, 26, 12, 27, 24, 22, 24, 62],
               'Расшифровка сумм и нулевых начислений по периодам')
    for index, item in enumerate(data['rows']):
        possible = item.get('possible')
        if possible is None:
            note = 'Нет данных о премиальной базе.'
        elif item['amount'] == 0 and possible > 0:
            note = 'База положительная, начисление нулевое. Проверьте выполнение плана, порог и поправочные коэффициенты.'
        elif possible == 0:
            note = 'Нулевая премиальная база. Проверьте отгрузки и ставки периода.'
        else:
            note = 'Рассчитано; выплата не подтверждена.'
        if item['unallocated']:
            note = 'Не распределено. ' + note
        row(ws, [item['subdivision'], item['author'], item['kind'], item['period'], item['currency'],
                 possible, item['amount'], item['bonus'], item['total'], note], 'alternate' if index % 2 else 'body')
    ws.auto_filter.ref = 'A5:J' + str(max(5, ws._portal_row))

    if display.get('has_annual_accrual'):
        quarters = [(index, column) for index, column in enumerate(display['period_columns']) if column['code'] != '5']
        if quarters:
            ws = sheet('В годовую по кварталам', ['Подразделение', 'Сотрудник', 'Вид мотивации',
                       *[column['code'] + ' кв. · В годовую премию' for _, column in quarters],
                       'Итого за выбранные кварталы', 'Валюта'],
                       [26, 44, 22, *([26] * len(quarters)), 30, 12],
                       'Годовая часть отгрузок квартала с годовым коэффициентом · До проверки годового условия выплаты')
            for section in display['team_sections']:
                for employee in section['rows']:
                    row(ws, [employee['subdivision'], employee['author'], section['kind'],
                             *[employee['period_cells'][index]['annual_accrual'] for index, _ in quarters],
                             employee['annual_accrual'], currency])
                row(ws, ['ИТОГО · ' + section['kind'], None, section['kind'],
                         *[section['period_summary_cells'][index]['annual_accrual'] for index, _ in quarters],
                         section['annual_accrual'], currency], 'subtotal')
            row(ws, ['ИТОГО', None, None,
                     *[display['period_cards'][index]['annual_accrual'] for index, _ in quarters],
                     display['annual_accrual'], currency], 'grand')

    if 'plan_fact' in data:
        ws = sheet('План-факт', ['Подразделение', 'Вид мотивации', 'База мотивации', 'Сотрудник / область', 'Период',
                   'План мотивации, USD', 'Факт мотивации, USD', 'Выполнение мотивации'],
                   [28, 24, 34, 44, 30, 27, 27, 27], 'Показатели подразделений · План и факт в USD')
        for index, item in enumerate(data['plan_fact']):
            row(ws, [item['subdivision'], item['kind'], item.get('formula', 'Подразделение'), item.get('scope_author', 'Всё подразделение'), item['period'],
                     item['plan'], item['fact'], item['ratio']],
                'alternate' if index % 2 else 'body', percent_columns=(7,))
        ws.auto_filter.ref = 'A5:H' + str(max(5, ws._portal_row))

    ws = sheet('Параметры', ['Параметр', 'Значение'], [38, 100],
               'Источник расчёта, параметры и выбранные условия')
    from .reports import PILOT_BONUS_NOTE
    filters = []
    if query is not None:
        for key, caption in [('subdivision', 'Подразделения'), ('type', 'Виды мотивации'),
                             ('author', 'Сотрудники'), ('period', 'Периоды')]:
            filters.append((caption, ', '.join(query.getlist(key)) or 'Все доступные'))
    for label, value in [*metadata, *filters, ('Доплата за кварталы', PILOT_BONUS_NOTE)]:
        row(ws, [str(label), str(value)])
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
