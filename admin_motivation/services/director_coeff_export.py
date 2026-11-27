"""One-sheet director coefficient export for the selected saved version."""
from io import BytesIO
from functools import lru_cache
from django.core.exceptions import ValidationError
from openpyxl import Workbook
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from ..models import GlobalCoeffVersion, SubdivisionDirectorCoeff
from .director_formula import resolve
from .active_directors import coefficient_rows as active_director_rows
from .subdivision_aliases import coefficient_rows as merged_rows


def director_coefficients_workbook(version, *, subdivision=None, employee=None):
    source = SubdivisionDirectorCoeff.objects.filter(version=version).select_related(
        'employee','subdivision','goods','variation_calculate').order_by(
        'employee__full_name','employee__employee_code','subdivision__subdivision_key',
        'goods__planning_group_sales','goods__group','goods__brand','goods_id')
    if employee is not None:
        source=source.filter(employee=employee)
    # Coefficient history is retained after unbinding/archive. Export only the
    # current roster, then apply the portal's existing subdivision merge policy.
    current = active_director_rows(source.iterator(chunk_size=2000))
    rows = list(merged_rows(((row.employee_id,row.subdivision.subdivision_key,row.goods_id,row)
                            for row in current),1,[0,1,2]))
    if subdivision is not None:
        from .subdivision_aliases import mapping,canonical
        selected_key=canonical(subdivision.subdivision_key,mapping())
        rows=[row for row in rows if row[1]==selected_key]
    pairs = {(subdivision,row.employee_id):row.employee for _,subdivision,_,row in rows}
    pairs = sorted(pairs.items(),key=lambda item:(item[0][0],item[1].full_name,item[1].employee_code,item[0][1]))
    goods = {row.goods_id:row.goods for _,_,_,row in rows}
    goods = sorted(goods.values(),key=lambda good:(good.planning_group_sales,good.group,good.brand,good.pk))
    matrix = {(row.goods_id,subdivision,row.employee_id):row for _,subdivision,_,row in rows}
    @lru_cache(maxsize=None)
    def values_for(good,pair):
        branch,employee_id=pair
        row=matrix.get((good.pk,branch,employee_id))
        return tuple(getattr(row,f'k{i}') for i in range(5))+(resolve(version,good,branch,row)[0],) if row else (None,)*6
    # Equality includes every saved numeric rate, effective formula and absent row.
    # Display rounding must never collapse genuinely different settings.
    grouped={}
    for pair,person in pairs:
        signature=tuple(values_for(good,pair) for good in goods)
        grouped.setdefault(signature,[]).append((pair,person))
    blocks=list(grouped.values())
    columns=4+6*len(blocks)
    if columns>16384:raise ValidationError('Слишком много директоров для одного листа Excel.')
    book = Workbook()
    sheet = book.active
    sheet.title = 'Директора'
    sheet.sheet_view.showGridLines = False
    sheet.sheet_view.zoomScale = 85
    sheet.merge_cells(start_row=1,start_column=1,end_row=1,end_column=columns)
    sheet['A1'] = (f'Коэффициенты действующих директоров · {version} · '
                   f'{version.get_status_display()} · ревизия {version.revision}')
    if subdivision is not None:sheet['A1']=sheet['A1'].value+f' · {subdivision.subdivision_name}'
    if employee is not None:sheet['A1']=sheet['A1'].value+f' · {employee.full_name}'
    sheet['A1'].font = Font(name='Calibri',size=12,bold=True,color='27364D')
    sheet['A1'].alignment = Alignment(vertical='center',wrap_text=True)
    sheet.row_dimensions[1].height = 35
    for column,label in enumerate(['№','Группа планов продаж','Группа ERP','Марка / бренд'],1):
        sheet.merge_cells(start_row=2,start_column=column,end_row=4,end_column=column)
        sheet.cell(2,column,label)
    last_subdivision,branch_start=None,None
    for index,members in enumerate(blocks):
        first=5+6*index
        branches=list(dict.fromkeys(pair[0] for pair,person in members))
        branch_label=' / '.join(branches)
        if branch_label!=last_subdivision:
            if branch_start is not None:sheet.merge_cells(start_row=2,start_column=branch_start,end_row=2,end_column=first-1)
            sheet.cell(2,first,branch_label).data_type='s'
            branch_start,last_subdivision=first,branch_label
        sheet.merge_cells(start_row=3,start_column=first,end_row=3,end_column=first+5)
        names=[person.full_name+(' · '+pair[0] if len(branches)>1 else '') for pair,person in members]
        sheet.cell(3,first,'\n'.join(names)).data_type='s'
        sheet.cell(3,first).comment=Comment('Одинаковые K0–K4 и эффективные формулы по всем товарам.\n'+
            '\n'.join(f'{pair[0]} · {person.full_name} · {person.employee_code}' for pair,person in members),'Портал')
        for offset,label in enumerate(['K0','K1','K2','K3','K4','Настройки формулы']):sheet.cell(4,first+offset,label)
    if branch_start is not None:sheet.merge_cells(start_row=2,start_column=branch_start,end_row=2,end_column=columns)
    line = Side(style='thin',color='DDE4EF')
    header_font = Font(name='Calibri',size=10,bold=True,color='263A56')
    for row in sheet.iter_rows(min_row=2,max_row=4,max_col=columns):
        for cell in row:
            cell.fill = PatternFill('solid',fgColor='F0ECFF' if cell.column > 4 else 'F3F6FB')
            cell.font = header_font
            cell.alignment = Alignment(horizontal='center',vertical='center',wrap_text=True)
            cell.border = Border(bottom=line,right=line)
    for row,height in [(2,27),(3,max(33,17*max((len(block) for block in blocks),default=1))),(4,25)]: sheet.row_dimensions[row].height = height
    fonts = {bold:Font(name='Calibri',size=10,color='263A56',bold=bold) for bold in (False,True)}
    body_border = Border(bottom=line)
    fills = {(band,odd):PatternFill('solid',fgColor=(('F5F1FF' if odd else 'FAF8FF') if band else ('F7F9FC' if odd else 'FFFFFF')))
             for band in (False,True) for odd in (False,True)}
    text_alignment = Alignment(vertical='center',wrap_text=True)
    numeric_alignment = Alignment(vertical='center',horizontal='right')
    for index,good in enumerate(goods,1):
        values = [index,good.planning_group_sales,good.group,good.brand]
        for members in blocks:
            pair,person=members[0]
            values.extend(values_for(good,pair))
        sheet.append(values)
        for column in range(1,columns+1):
            cell = sheet.cell(index+4,column)
            numeric = column > 4 and (column-5)%6 < 5
            cell.font = fonts[column == 4]
            cell.alignment = numeric_alignment if numeric else text_alignment
            cell.fill = fills[numeric,bool(index%2)]
            cell.border = body_border
            if numeric: cell.number_format = '0.00%'
            elif column > 1 and cell.value is not None:
                cell.data_type = 's'
                if column>4 and (column-5)%6==5:
                    members=blocks[(column-5)//6];pair,person=members[0];row=matrix.get((good.pk,*pair))
                    if row:cell.comment=Comment('\n'.join(f'{person.full_name} · {pair[0]}: '+resolve(version,good,pair[0],matrix[(good.pk,*pair)])[1] for pair,person in members),'Портал')
        sheet.row_dimensions[index+4].height = 29
    if not goods:
        sheet.merge_cells(start_row=5,start_column=1,end_row=5,end_column=columns)
        sheet['A5'] = 'Коэффициенты действующих директоров в выбранной версии не заданы.'
    sheet.freeze_panes = 'E5'
    sheet.auto_filter.ref = f'A4:{get_column_letter(columns)}{len(goods)+4}'
    for column,width in enumerate([6,25,36,23],1):
        sheet.column_dimensions[get_column_letter(column)].width = width
    for column in range(5,columns+1):
        sheet.column_dimensions[get_column_letter(column)].width = 34 if (column-5)%6 == 5 else 11
    sheet.sheet_properties.pageSetUpPr.fitToPage = True
    sheet.page_setup.orientation = 'landscape'
    sheet.page_setup.paperSize = sheet.PAPERSIZE_A3
    sheet.page_setup.fitToWidth = 1
    sheet.page_setup.fitToHeight = 0
    sheet.print_title_rows = '1:4'
    sheet.print_area = f'A1:{get_column_letter(columns)}{max(len(goods)+4,5)}'
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
