"""Excel export of the immutable, private comparison snapshot."""
from io import BytesIO
from decimal import Decimal
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter

SUMMARY=[('scenario','Вариант'),('year','Год'),('branch','Подразделение'),('employee','Сотрудник'),
 ('role','Роль'),('rate_source','Источник ставок'),('q1','I квартал, USD'),('q2','II квартал, USD'),
 ('q3','III квартал, USD'),('q4','IV квартал, USD'),('annual_accrued','Годовая часть начислена, USD'),
 ('annual_paid','Годовая часть выплачена, USD'),('bonus','Доплата, USD'),('total','К выплате, USD'),('notes','Примечание')]
DETAIL=[('scenario','Вариант'),('employee','Сотрудник'),('rate_source','Источник ставок'),('branch','Подразделение'),
 ('formula','Формула'),('planning','Группа планов продаж'),('goods','Группа товаров'),('brand','Бренд'),
 ('period','Период'),('plan','План, USD'),('fact','Факт, USD'),('ratio','Выполнение'),
 ('department_ratio','Выполнение подразделения'),('threshold','Порог'),('correction','Поправка периода'),
 ('possible','Начислено, USD'),('paid','Выплачено, USD'),('bonus','Доплата, USD'),('rate_origin','Подбор ставки'),('note','Причина')]
MONEY={'q1','q2','q3','q4','annual_accrued','annual_paid','bonus','total','plan','fact','possible','paid'}
PERCENT={'ratio','department_ratio','threshold'}


def table(book,title,columns,rows):
    sheet=book.create_sheet(title);sheet.append([label for key,label in columns])
    for row in rows:
        for col,(key,label) in enumerate(columns,1):
            value=row.get(key)
            if isinstance(value,list):value='; '.join(str(x) for x in value)
            if value is not None and key in MONEY|PERCENT|{'correction'}:value=Decimal(str(value))
            cell=sheet.cell(sheet.max_row+1 if col==1 else sheet.max_row,col,value)
            # User-controlled names and descriptions are always text, never formulas.
            if isinstance(value,str):cell.data_type='s'
            if key in MONEY:cell.number_format='#,##0.00;[Red]-#,##0.00'
            elif key in PERCENT:cell.number_format='0.00%'
            cell.alignment=Alignment(vertical='top',wrap_text=True)
    sheet.freeze_panes='G2';sheet.auto_filter.ref=sheet.dimensions
    for cell in sheet[1]:cell.font=Font(bold=True,color='FFFFFF');cell.fill=PatternFill('solid',fgColor='6650BE');cell.alignment=Alignment(wrap_text=True)
    sheet.row_dimensions[1].height=44
    for col,(key,label) in enumerate(columns,1):sheet.column_dimensions[get_column_letter(col)].width=32 if key in {'employee','rate_source','branch','note','notes'} else 22
    return sheet


def workbook(snapshot):
    data=snapshot.results;book=Workbook();book.remove(book.active)
    rows=[]
    for original in data['rows']:
        row=dict(original)
        for q in range(1,5):row[f'q{q}']=row['quarters'][q-1] if row['year']==2025 or q<=3 else None
        rows.append(row)
    table(book,'Сравнение',SUMMARY,rows)
    for case in data['scenarios']:
        table(book,f"{case['id']} Вариант",SUMMARY,[row for row in rows if row['scenario']==case['id']])
    table(book,'Выполнение',DETAIL,data['details'])
    rate_rows=[{**row,**{f'k{i}':value for i,value in enumerate(row['values'])}} for row in data['rate_inputs']]
    table(book,'Ставки',[('employee','Источник ставок'),('branch','Подразделение'),('planning','Группа планов продаж'),
        ('goods','Группа товаров'),('brand','Бренд'),*[(f'k{i}',f'K{i}') for i in range(5)],
        ('formula','Формула'),('origin','Подбор ставки')],rate_rows)
    for row in book['Ставки'].iter_rows(min_row=2,min_col=6,max_col=10):
        for cell in row:
            cell.value=Decimal(str(cell.value));cell.number_format='0.00%'
    parameters=[{'name':'Расчёт','value':f'№ {snapshot.pk} · {snapshot.created_at.isoformat()}'},
        {'name':'Правило','value':data['rule']},{'name':'Валюта','value':'USD'},
        {'name':'Коэффициенты 2026','value':f"{data['version']['title']} · ревизия {data['version']['revision']}"},
        {'name':'Роли','value':'Назначения действуют только внутри сравнения; действующий состав не изменён'},
        {'name':'Факт = план','value':'Оборот заменяется планом с сегментами O0–O4; условия выполнения проверяются заново'},
        {'name':'2026 I–III кварталы','value':'Годовая часть показана как начисленная, в выплату не включена. IV квартал не рассчитывается'},
        {'name':'Поправки','value':'Поправки подразделения по периодам взяты из выбранной версии модели 2026'},
        {'name':'Менеджеры 2025','value':'Сохранённые выплаты Политики из исходного пересчёта 2025, без применения директорских ставок'},
        {'name':'Порог','value':'I квартал без порога; II — I+II; III — III; IV и год — весь год; неположительный план не определяет выполнение'}]
    for case in data['scenarios']:parameters.append({'name':f"Вариант {case['id']}",'value':case['title']})
    for year,source in data['sources'].items():
        parameters.append({'name':f'Источник {year}','value':f"{source['run_id']} · {source['completed_at']} · план {data['plans'][year]['source_version']}"})
        for key,value in source['parameters'].items():parameters.append({'name':f'{year}: {key}','value':str(value)})
    table(book,'Параметры',[('name','Параметр'),('value','Значение')],parameters)
    book['Параметры'].column_dimensions['B'].width=110;book['Параметры'].freeze_panes='A2'
    output=BytesIO();book.save(output);output.seek(0);return output
