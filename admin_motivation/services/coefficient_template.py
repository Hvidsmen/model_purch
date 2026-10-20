"""Generate the two-header XLSX layout accepted by coefficient file import."""
from io import BytesIO
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.workbook.defined_name import DefinedName
from ..models import GlobalCoeff, SubdivisionCoeff, SegmentCoeff, GlobalDirectorCoeff, SubdivisionDirectorCoeff, VariationCalculate


def coefficient_template(version, subdivision=None, employee=None):
    segments = list(SegmentCoeff.objects.order_by('pk').values_list('pk', 'segment_name'))
    model = SubdivisionCoeff if subdivision else GlobalCoeff
    rows = model.objects.filter(version=version)
    if subdivision:
        rows = rows.filter(subdivision=subdivision)
    grouped = {}
    for goods, pg, group, brand, kind, segment, value, kind_id, cell_id, formula in rows.order_by().values_list(
            'goods_id', 'goods__planning_group_sales', 'goods__group', 'goods__brand',
            'type_coeff__type_coeff_name', 'segment_id', 'motivation_coeff', 'type_coeff_id', 'pk', 'variation_calculate__variation_name').iterator(chunk_size=2000):
        if kind not in ('Политики', 'Продажи'):
            continue
        record = grouped.setdefault((pg, group, brand, goods), {})
        record[kind, segment] = value
        priority = (kind_id, segment, cell_id)
        if 'formula_key' not in record or priority < record['formula_key']:
            record['formula_key'], record['formula'] = priority, formula
    directors = (SubdivisionDirectorCoeff.objects.filter(version=version, subdivision=subdivision, employee=employee)
                 if subdivision and employee else GlobalDirectorCoeff.objects.filter(version=version)
                 if not subdivision else SubdivisionDirectorCoeff.objects.none())
    for goods, pg, group, brand, *values in directors.order_by().values_list(
            'goods_id', 'goods__planning_group_sales', 'goods__group', 'goods__brand', *[f'k{i}' for i in range(5)]):
        record = grouped.setdefault((pg, group, brand, goods), {})
        for index, value in enumerate(values):
            record['Директор', index] = float(value)
    book = Workbook()
    sheet = book.active
    sheet.title = 'Коэффициенты'
    labels = ['№', 'Группа планов продаж', 'Группа ERP', 'Марка / бренд']
    count = len(segments)
    last_column = 10 + 2 * count
    bands = [(5, 4 + count, 'Политики', 'EFF6FF', 'F8FBFF'),
             (5 + count, 4 + 2 * count, 'Продажи', 'ECFDF5', 'F5FCFA'),
             (5 + 2 * count, last_column - 1, 'Директор', 'F3F0FF', 'FAF8FF')]
    sheet.append([*labels, *[None] * (last_column - 4)])
    sheet.append([None] * 4 + [name for pk, name in segments] * 2 + [f'K{i}' for i in range(5)] + [None])
    for column, label in enumerate(labels, 1):
        sheet.merge_cells(start_row=1, start_column=column, end_row=2, end_column=column)
    sheet.cell(1, last_column, 'Настройки формулы')
    sheet.merge_cells(start_row=1, start_column=last_column, end_row=2, end_column=last_column)
    for first, last, title, header_color, body_color in bands:
        if last >= first:
            sheet.merge_cells(start_row=1, start_column=first, end_row=1, end_column=last)
            sheet.cell(1, first, title)
    for index, (key, cells) in enumerate(sorted(grouped.items()), 1):
        sheet.append([index, *key[:3], *[cells.get((kind, segment))
            for kind in ('Политики', 'Продажи') for segment, name in segments], *[cells.get(('Директор', i)) for i in range(5)], cells.get('formula')])
        # Excel must treat external classification names as literal text.
        for column in [2, 3, 4, last_column]:
            sheet.cell(sheet.max_row, column).data_type = 's'
    sheet.freeze_panes = 'E3'
    sheet.sheet_view.showGridLines = False
    sheet.sheet_view.zoomScale = 90
    sheet.auto_filter.ref = f'A2:{get_column_letter(last_column)}{max(sheet.max_row, 2)}'
    edge = Side(style='thin', color='DCE4F0')
    divider = Side(style='medium', color='CBD5E1')
    for row in sheet.iter_rows():
        for cell in row:
            column = cell.column
            band = next((band for band in bands if band[0] <= column <= band[1]), None)
            background = (band[3] if band else 'F5F7FC') if cell.row <= 2 else (
                band[4] if band else ('F8FAFD' if cell.row % 2 == 0 else 'FFFFFF'))
            cell.fill = PatternFill('solid', fgColor=background)
            cell.font = Font(name='Calibri', size=11, color='1E3A5F', bold=cell.row <= 2 or column == 4)
            cell.border = Border(left=divider if band and column == band[0] else edge,
                                 right=edge, top=edge, bottom=edge)
            cell.alignment = Alignment(horizontal='center' if cell.row <= 2 or column == 1 else
                                       'right' if 5 <= column < last_column else 'left', vertical='center',
                                       wrap_text=cell.row <= 2, indent=1 if cell.row > 2 and 2 <= column <= 4 else 0)
            if cell.row > 2 and 5 <= column < last_column:
                cell.number_format = '0.00%'
    sheet.row_dimensions[1].height = 30
    sheet.row_dimensions[2].height = 25
    for row in range(3, sheet.max_row + 1):
        sheet.row_dimensions[row].height = 25
    for column, width in [('A', 6), ('B', 26), ('C', 38), ('D', 24)]:
        sheet.column_dimensions[column].width = width
    for column in range(5, last_column + 1):
        sheet.column_dimensions[get_column_letter(column)].width = 10
    sheet.column_dimensions[get_column_letter(last_column)].width = 30
    sheet.print_title_rows = '1:2'
    sheet.print_options.horizontalCentered = True
    sheet.page_setup.orientation = 'landscape'
    sheet.page_setup.paperSize = sheet.PAPERSIZE_A3
    sheet.page_setup.fitToWidth = 1
    sheet.page_setup.fitToHeight = 0
    sheet.sheet_properties.pageSetUpPr.fitToPage = True
    instructions = book.create_sheet('Инструкция')
    notes = ['Текущие коэффициенты выбранной версии',
             f'Версия: {version}' if version else 'Версия не выбрана',
             f'Подразделение: {subdivision.subdivision_name}' if subdivision else 'Глобальные коэффициенты',
             f'Директор: {employee.full_name}' if employee else '',
             'Редактируйте значения на листе «Коэффициенты». Первые две строки не удаляйте.',
             'Коэффициенты отображаются в процентах: 0,48% соответствует значению 0,0048.',
             'Для обратной загрузки сохраните файл в формате .xlsx и порядок столбцов.',
             'В столбце «Настройки формулы» выберите нужную формулу из списка.',
             'Пустые ячейки политик и продаж сохраняют существующие значения.',
             'Группа директора: заполните все K0–K4 или оставьте всю группу пустой.']
    for text in filter(None, notes):
        cell = instructions.cell(instructions.max_row + 1 if instructions.cell(1, 1).value else 1, 1, text)
        cell.data_type = 's'
        cell.font = Font(name='Calibri', size=11, color='1E3A5F')
        cell.alignment = Alignment(wrap_text=True, vertical='center')
        instructions.row_dimensions[cell.row].height = 30
    instructions.column_dimensions['A'].width = 110
    instructions.sheet_view.showGridLines = False
    instructions.cell(1, 3, 'Доступные формулы')
    formulas = list(VariationCalculate.objects.order_by('pk').values_list('variation_name', flat=True))
    for row, name in enumerate(formulas, 2):
        instructions.cell(row, 3, name).data_type = 's'
    instructions.column_dimensions['C'].width = 40
    if formulas:
        book.defined_names.add(DefinedName('MotivationFormulaOptions',
            attr_text=f"'Инструкция'!$C$2:$C${len(formulas) + 1}"))
        validation = DataValidation(type='list', formula1='=MotivationFormulaOptions', allow_blank=True)
        validation.errorTitle = 'Неизвестная формула'
        validation.error = 'Выберите формулу из списка на листе «Инструкция».'
        validation.showErrorMessage = True
        validation.errorStyle = 'stop'
        sheet.add_data_validation(validation)
        validation.add(f'{get_column_letter(last_column)}3:{get_column_letter(last_column)}{max(sheet.max_row, 10000)}')
    stream = BytesIO()
    book.save(stream)
    book.close()
    return stream.getvalue()
