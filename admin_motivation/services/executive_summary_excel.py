"""Export the displayed executive subdivision comparison, with Q4/year outline."""
from io import BytesIO
from openpyxl import Workbook
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.styles import Font,PatternFill,Alignment
from openpyxl.utils import get_column_letter

COLUMNS=[('name','Подразделение / вид мотивации / директор'),('plan','План продаж, USD'),('ddp','DDP, USD'),
 ('global','Мотивация глобальная, USD'),('global_quarter','Глобальная: IV квартал, USD'),
 ('global_annual','Глобальная: годовая часть из IV квартала, USD'),('global_percent','Глобальная мотивация, % от плана'),
 ('global_markup','Наценка глобальная'),('local','Мотивация подразделений, USD'),
 ('local_percent','Мотивация подразделений, % от плана'),('local_markup','Наценка подразделений'),
 ('difference','Разница, USD'),('markup_difference','Отклонение наценки')]


def text(cell,value):
    cell.value=ILLEGAL_CHARACTERS_RE.sub('',value)[:32767];cell.data_type='s'


def workbook(data,metadata,title=None):
    book=Workbook();sheet=book.active;sheet.title='Сравнение'
    sheet.merge_cells('A1:M1');text(sheet['A1'],title or 'Сравнение по подразделениям')
    sheet['A1'].font=Font(bold=True,size=14,color='27364D');sheet.row_dimensions[1].height=32
    for index,(key,label) in enumerate(COLUMNS,1):
        cell=sheet.cell(2,index);text(cell,label)
        cell.font=Font(bold=True,color='FFFFFF');cell.fill=PatternFill('solid',fgColor='6650BE')
        cell.alignment=Alignment(wrap_text=True,vertical='center')
    sheet.row_dimensions[2].height=58
    def append(node,level=0,company=False):
        row=sheet.max_row+1
        for index,(key,_) in enumerate(COLUMNS,1):
            value='Итого по компании' if key=='name' and company else node.get(key)
            if value is not None and key in ('global_percent','local_percent'):value=value/100
            cell=sheet.cell(row,index)
            if isinstance(value,str):text(cell,value)
            else:cell.value=value
            cell.number_format='0.00%' if key in ('global_percent','local_percent') else '#,##0.00'
            cell.alignment=Alignment(vertical='top',wrap_text=key=='name',indent=level if key=='name' else 0)
            if company or level==0:
                cell.fill=PatternFill('solid',fgColor='EEE9FC' if company else 'F5F3FC');cell.font=Font(bold=True)
        if not company:
            for child in node.get('children',[]):append(child,level+1)
    if data:
        append(data[0],company=True)
        for node in data[1:]:append(node)
    sheet.freeze_panes='B3';sheet.auto_filter.ref=f'A2:M{sheet.max_row}'
    for index in range(1,14):sheet.column_dimensions[get_column_letter(index)].width=42 if index==1 else 21
    sheet.sheet_properties.outlinePr.summaryRight=False
    sheet.sheet_properties.outlinePr.showOutlineSymbols=True;sheet.sheet_view.showOutlineSymbols=True
    sheet.column_dimensions.group('E','F',outline_level=1,hidden=True);sheet.column_dimensions['D'].collapsed=True
    sheet.page_setup.orientation='landscape';sheet.print_title_rows='1:2'
    parameters=book.create_sheet('Параметры');parameters.append(['Параметр','Значение'])
    for label,value in [*metadata,('Разбивка премии','Основной вариант: IV квартал и годовая часть из IV квартала, с учётом выбранных периодов')]:
        row=parameters.max_row+1;text(parameters.cell(row,1),str(label));text(parameters.cell(row,2),str(value))
    parameters.freeze_panes='A2';parameters.column_dimensions['A'].width=28;parameters.column_dimensions['B'].width=100
    for row in parameters:
        for cell in row:cell.alignment=Alignment(wrap_text=True,vertical='top')
    output=BytesIO();book.save(output);book.close();output.seek(0);return output
