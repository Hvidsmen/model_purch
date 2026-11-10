"""One-sheet director coefficient export for the selected saved version."""
from io import BytesIO
from django.core.exceptions import ValidationError
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from ..models import GlobalCoeffVersion, SubdivisionDirectorCoeff
from .sql_export import director_variation
from .active_directors import coefficient_rows as active_director_rows
from .subdivision_aliases import coefficient_rows as merged_rows


def director_coefficients_workbook(version):
    source = SubdivisionDirectorCoeff.objects.filter(version=version).select_related(
        'employee','subdivision','goods','variation_calculate').order_by(
        'employee__full_name','employee__employee_code','subdivision__subdivision_key',
        'goods__planning_group_sales','goods__group','goods__brand','goods_id')
    # Coefficient history is retained after unbinding/archive. Export only the
    # current roster, then apply the portal's existing subdivision merge policy.
    current = active_director_rows(source.iterator(chunk_size=2000))
    rows = list(merged_rows(((row.employee_id,row.subdivision.subdivision_key,row.goods_id,row)
                            for row in current),1,[0,1,2]))
    rows.sort(key=lambda item:(item[3].employee.full_name,item[3].employee.employee_code,item[1],
                               item[3].goods.planning_group_sales,item[3].goods.group,item[3].goods.brand,item[2]))
    book = Workbook()
    sheet = book.active
    sheet.title = 'Директора'
    sheet.sheet_view.showGridLines = False
    sheet.sheet_view.zoomScale = 85
    sheet.merge_cells('A1:L1')
    sheet['A1'] = (f'Коэффициенты действующих директоров · {version} · '
                   f'{version.get_status_display()} · ревизия {version.revision}')
    sheet['A1'].font = Font(name='Calibri',size=12,bold=True,color='27364D')
    sheet['A1'].alignment = Alignment(vertical='center',wrap_text=True)
    sheet.row_dimensions[1].height = 35
    labels = ['№','Директор','Подразделение','Группа планов продаж','Группа ERP','Марка / бренд']
    for column,label in enumerate(labels,1):
        sheet.merge_cells(start_row=2,start_column=column,end_row=3,end_column=column)
        sheet.cell(2,column,label)
    sheet.merge_cells('G2:K2'); sheet['G2'] = 'Директор'
    for index in range(5): sheet.cell(3,7+index,f'K{index}')
    sheet.merge_cells('L2:L3'); sheet['L2'] = 'Настройки формулы'
    line = Side(style='thin',color='DDE4EF')
    for row in sheet.iter_rows(min_row=2,max_row=3,max_col=12):
        for cell in row:
            cell.fill = PatternFill('solid',fgColor='F0ECFF' if 7 <= cell.column <= 11 else 'F3F6FB')
            cell.font = Font(name='Calibri',size=10,bold=True,color='263A56')
            cell.alignment = Alignment(horizontal='center',vertical='center',wrap_text=True)
            cell.border = Border(bottom=line,right=line)
    sheet.row_dimensions[2].height = 29
    sheet.row_dimensions[3].height = 23
    fonts = {bold:Font(name='Calibri',size=10,color='263A56',bold=bold) for bold in (False,True)}
    body_border = Border(bottom=line)
    fills = {(band,odd):PatternFill('solid',fgColor=(('F5F1FF' if odd else 'FAF8FF') if band else ('F7F9FC' if odd else 'FFFFFF')))
             for band in (False,True) for odd in (False,True)}
    alignments = {column:Alignment(vertical='center',horizontal='right' if 7 <= column <= 11 else 'left',
                                  wrap_text=column in (2,3,4,5,6,12)) for column in range(1,13)}
    count = 0
    for count,(_,subdivision,_,row) in enumerate(rows,1):
        formula = row.variation_calculate.variation_name if row.variation_calculate_id else version.fallback_variation or director_variation(row.goods)
        values = [count,row.employee.full_name,subdivision,
                  row.goods.planning_group_sales,row.goods.group,row.goods.brand,
                  *[getattr(row,f'k{index}') for index in range(5)],formula]
        sheet.append(values)
        for column in range(1,13):
            cell = sheet.cell(count+3,column)
            cell.font = fonts[column == 6]
            cell.alignment = alignments[column]
            cell.fill = fills[7 <= column <= 11,bool(count % 2)]
            cell.border = body_border
            if column in (2,3,4,5,6,12): cell.data_type = 's'
            if 7 <= column <= 11: cell.number_format = '0.00%'
        sheet.row_dimensions[count+3].height = 29
    if not count:
        sheet.merge_cells('A4:L4')
        sheet['A4'] = 'Коэффициенты действующих директоров в выбранной версии не заданы.'
    sheet.freeze_panes = 'G4'
    sheet.auto_filter.ref = f'A3:L{count+3}'
    for column,width in enumerate([6,32,24,25,36,23,11,11,11,11,11,36],1):
        sheet.column_dimensions[get_column_letter(column)].width = width
    sheet.sheet_properties.pageSetUpPr.fitToPage = True
    sheet.page_setup.orientation = 'landscape'
    sheet.page_setup.paperSize = sheet.PAPERSIZE_A3
    sheet.page_setup.fitToWidth = 1
    sheet.page_setup.fitToHeight = 0
    sheet.print_title_rows = '1:3'
    sheet.print_area = f'A1:L{max(count+3,4)}'
    # Exporting a draft must not silently mix edits from different revisions.
    if not GlobalCoeffVersion.objects.filter(pk=version.pk,revision=version.revision).exists():
        book.close()
        raise ValidationError('Версия изменилась во время выгрузки. Повторите скачивание.')
    output = BytesIO()
    try:
        book.save(output)
    finally:
        book.close()
    return output.getvalue()
