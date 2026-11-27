"""One readable policy/sales matrix, grouping identical physical branch settings."""
from io import BytesIO
from django.core.exceptions import ValidationError
from openpyxl import Workbook
from openpyxl.comments import Comment
from openpyxl.styles import Alignment,Font,PatternFill,Border,Side
from openpyxl.utils import get_column_letter
from ..models import SubdivisionCoeff,GlobalCoeffVersion
from .director_formula import resolve


def workbook(version):
    rows=SubdivisionCoeff.objects.filter(version=version,type_coeff__type_coeff_name__in=('Политики','Продажи')).select_related('subdivision','goods','type_coeff','segment','variation_calculate').order_by('subdivision__subdivision_key','goods_id','pk')
    goods={};branches={};cells={}
    for row in rows.iterator(chunk_size=2000):
        segment=row.segment.segment_name.upper().replace('К','K').replace('О','O')
        if len(segment)!=2 or segment[0] not in 'KO' or segment[1] not in '01234':continue
        goods[row.goods_id]=row.goods;branches[row.subdivision_id]=row.subdivision
        key=(row.type_coeff.type_coeff_name,row.subdivision_id,row.goods_id,int(segment[1]))
        if key in cells:raise ValidationError('Повтор ставки для одной ячейки. Проверьте матрицу подразделения.')
        cells[key]=row
    goods=sorted(goods.values(),key=lambda g:(g.planning_group_sales,g.group,g.brand,g.pk))
    def values(kind,branch,good):
        source=[cells.get((kind,branch.pk,good.pk,i)) for i in range(5)]
        if not any(source):return (None,)*6
        formulas=[resolve(version,good,branch.subdivision_key,row,kind=kind)[0] if row else None for row in source]
        present={f for f in formulas if f is not None}
        formula=next(iter(present)) if len(present)==1 else '; '.join(f'K{i}: {f}' for i,f in enumerate(formulas) if f is not None)
        return (*[row.motivation_coeff if row else None for row in source],formula)
    blocks=[]
    for kind in ('Политики','Продажи'):
        grouped={}
        for branch in sorted(branches.values(),key=lambda b:(b.subdivision_key,b.pk)):
            signature=tuple(values(kind,branch,g) for g in goods)
            if any(any(v is not None for v in row) for row in signature):grouped.setdefault(signature,[]).append(branch)
        blocks.extend((kind,signature,members) for signature,members in grouped.items())
    columns=4+6*len(blocks)
    if columns>16384:raise ValidationError('Слишком много различных наборов коэффициентов для одного листа Excel.')
    book=Workbook();sheet=book.active;sheet.title='Подразделения';sheet.sheet_view.showGridLines=False;sheet.sheet_view.zoomScale=85
    sheet.merge_cells(start_row=1,start_column=1,end_row=1,end_column=columns)
    sheet['A1']=f'Коэффициенты подразделений · {version} · {version.get_status_display()} · ревизия {version.revision}'
    sheet['A1'].font=Font(name='Calibri',size=12,bold=True,color='27364D');sheet['A1'].alignment=Alignment(wrap_text=True,vertical='center');sheet.row_dimensions[1].height=35
    for c,label in enumerate(('№','Группа планов продаж','Группа ERP','Марка / бренд'),1):
        sheet.merge_cells(start_row=2,start_column=c,end_row=4,end_column=c);sheet.cell(2,c,label)
    for index,(kind,signature,members) in enumerate(blocks):
        first=5+index*6
        sheet.merge_cells(start_row=3,start_column=first,end_row=3,end_column=first+5)
        cell=sheet.cell(3,first,'\n'.join(b.subdivision_name for b in members));cell.data_type='s'
        cell.comment=Comment('Одинаковые K0–K4 и эффективные формулы по всем товарам. Пустые ячейки — отсутствие ставки.\n'+ '\n'.join(b.subdivision_key for b in members),'Портал')
        for offset,label in enumerate(('K0','K1','K2','K3','K4','Формула')):sheet.cell(4,first+offset,label)
    for kind in ('Политики','Продажи'):
        indices=[i for i,b in enumerate(blocks) if b[0]==kind]
        if indices:
            first,last=5+6*indices[0],10+6*indices[-1]
            sheet.merge_cells(start_row=2,start_column=first,end_row=2,end_column=last);sheet.cell(2,first,kind)
    line=Side(style='thin',color='DDE4EF')
    for row in sheet.iter_rows(min_row=2,max_row=4,max_col=columns):
        for cell in row:
            kind=blocks[(cell.column-5)//6][0] if cell.column>4 else None
            cell.fill=PatternFill('solid',fgColor='EDF3FF' if kind=='Политики' else 'EDFBF7' if kind else 'F3F6FB')
            cell.font=Font(name='Calibri',size=10,bold=True,color='263A56');cell.alignment=Alignment(horizontal='center',vertical='center',wrap_text=True);cell.border=Border(bottom=line,right=line)
    sheet.row_dimensions[2].height=27;sheet.row_dimensions[3].height=min(409,max(38,17*max((len(b[2]) for b in blocks),default=1)));sheet.row_dimensions[4].height=25
    for index,good in enumerate(goods):
        sheet.append([index+1,good.planning_group_sales,good.group,good.brand,*[value for kind,signature,members in blocks for value in signature[index]]])
        for cell in sheet[index+5]:
            numeric=cell.column>4 and (cell.column-5)%6<5
            cell.font=Font(name='Calibri',size=10,color='263A56',bold=cell.column==4)
            cell.alignment=Alignment(vertical='center',horizontal='right' if numeric else 'left',wrap_text=not numeric)
            cell.fill=PatternFill('solid',fgColor=('F6F8FD' if index%2 else 'FFFFFF'));cell.border=Border(bottom=line)
            if numeric:cell.number_format='0.00########%'
            elif isinstance(cell.value,str):cell.data_type='s'
        sheet.row_dimensions[index+5].height=29
    if not goods:sheet.cell(5,1,'В выбранной версии нет ставок политиков и продаж по подразделениям.')
    sheet.freeze_panes='E5';sheet.auto_filter.ref=f'A4:{get_column_letter(columns)}{max(5,len(goods)+4)}'
    for c,width in enumerate((6,25,36,23),1):sheet.column_dimensions[get_column_letter(c)].width=width
    for c in range(5,columns+1):sheet.column_dimensions[get_column_letter(c)].width=34 if (c-5)%6==5 else 12
    sheet.print_title_rows='1:4';sheet.sheet_properties.pageSetUpPr.fitToPage=True;sheet.page_setup.orientation='landscape';sheet.page_setup.paperSize=sheet.PAPERSIZE_A3;sheet.page_setup.fitToWidth=1;sheet.page_setup.fitToHeight=0
    if not GlobalCoeffVersion.objects.filter(pk=version.pk,revision=version.revision).exists():
        book.close();raise ValidationError('Версия изменилась во время выгрузки. Повторите скачивание.')
    output=BytesIO()
    try:book.save(output)
    finally:book.close()
    output.seek(0);return output
