"""Excel export of the immutable, private comparison snapshot."""
from io import BytesIO
from decimal import Decimal
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter

SCENARIO_DESCRIPTIONS={
    1:'За 2025 год целиком. Москва и Термотрейд отдельно; директорские ставки модели 2026.',
    2:'За 2026 год, I–III кварталы, как есть — фактическое выполнение. Москва и Термотрейд отдельно.',
    3:'За 2026 год, I–III кварталы, при выполнении на 100% — факт заменён планом. Москва и Термотрейд отдельно.',
    4:'Объединённый план Термотрейда и Москвы за I–III кварталы 2026, как есть. Каждый из двух директоров считается со всего объединённого подразделения.',
    5:'Объединённый план Термотрейда и Москвы за I–III кварталы 2026 при выполнении на 100% — факт заменён планом. Каждый из двух директоров считается со всего объединённого подразделения.',
}

SUMMARY=[('scenario','Вариант'),('description','Пояснение варианта'),('year','Год'),('branch','Подразделение'),('employee','Сотрудник'),
 ('role','Роль'),('rate_source','Источник ставок'),
 *[item for q,label in enumerate(('I','II','III','IV'),1) for item in
   ((f'q{q}_possible',f'{label} квартал: без учёта выполнения плана, USD'),(f'q{q}',f'{label} квартал: к выплате, USD'))],
 *[(f'annual_q{q}',f'В годовую за {label} квартал, USD') for q,label in enumerate(('I','II','III','IV'),1)],
 ('annual_accrued','Годовая часть начислена, USD'),
 ('annual_paid','Годовая часть выплачена, USD'),('bonus','Доплата, USD'),('total','К выплате, USD'),('notes','Примечание')]
DETAIL=[('scenario','Вариант'),('employee','Сотрудник'),('rate_source','Источник ставок'),('branch','Подразделение'),
 ('formula','Формула'),('planning','Группа планов продаж'),('goods','Группа товаров'),('brand','Бренд'),
 ('period','Период'),('plan','План, USD'),('fact','Факт, USD'),('ratio','Выполнение'),
 ('department_ratio','Выполнение подразделения'),('threshold','Порог'),('correction','Поправка периода'),
 ('possible','Начислено, USD'),('annual_contribution','В годовую за квартал, USD'),('paid','Выплачено, USD'),('bonus','Доплата, USD'),('rate_origin','Подбор ставки'),('note','Причина')]
MONEY={'q1','q2','q3','q4','annual_accrued','annual_paid','bonus','total','plan','fact','possible','paid','annual_contribution',*[f'annual_q{q}' for q in range(1,5)],*[f'q{q}_possible' for q in range(1,5)]}
PERCENT={'ratio','department_ratio','threshold'}



def fold_summary(sheet,columns):
    positions={key:index for index,(key,_) in enumerate(columns,1)}
    sheet.sheet_properties.outlinePr.summaryRight=True
    sheet.sheet_properties.outlinePr.showOutlineSymbols=True
    sheet.sheet_view.showOutlineSymbols=True
    for key in ('role','rate_source','annual_paid','bonus'):
        sheet.column_dimensions[get_column_letter(positions[key])].hidden=True
    for q in range(1,5):
        letter=get_column_letter(positions[f'q{q}_possible'])
        sheet.column_dimensions.group(letter,letter,outline_level=1,hidden=True)
        sheet.column_dimensions[get_column_letter(positions[f'q{q}'])].collapsed=True
    sheet.column_dimensions.group(get_column_letter(positions['annual_q1']),
        get_column_letter(positions['annual_q4']),outline_level=1,hidden=True)
    sheet.column_dimensions[get_column_letter(positions['annual_accrued'])].collapsed=True


def table(book,title,columns,rows,*,freeze_panes='G2'):
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
    sheet.freeze_panes=freeze_panes;sheet.auto_filter.ref=sheet.dimensions
    for cell in sheet[1]:cell.font=Font(bold=True,color='FFFFFF');cell.fill=PatternFill('solid',fgColor='6650BE');cell.alignment=Alignment(wrap_text=True)
    sheet.row_dimensions[1].height=44
    for col,(key,label) in enumerate(columns,1):sheet.column_dimensions[get_column_letter(col)].width=32 if key in {'employee','rate_source','branch','note','notes'} else 22
    for col,(key,_) in enumerate(columns,1):
        if key=='description':
            sheet.column_dimensions[get_column_letter(col)].width=72
            for row in range(2,sheet.max_row+1):sheet.row_dimensions[row].height=90
    if columns is SUMMARY:fold_summary(sheet,columns)
    return sheet


def workbook(snapshot):
    from .director_scenario_presenter import with_quarter_premiums
    data=with_quarter_premiums(snapshot.results);book=Workbook();book.remove(book.active)
    rows=[]
    for original in data['rows']:
        row=dict(original)
        row['description']=SCENARIO_DESCRIPTIONS[row['scenario']]
        for q in range(1,5):row[f'q{q}']=row['quarters'][q-1] if row['year']==2025 or q<=3 else None
        rows.append(row)
    table(book,'Сравнение',SUMMARY,rows,freeze_panes='H2')
    for case in data['scenarios']:
        table(book,f"{case['id']} Вариант",SUMMARY,[row for row in rows if row['scenario']==case['id']],freeze_panes='H2')
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
        {'name':'Участники 2025','value':'Только два получателя директорских ставок; собственные премии источников ставок не включены'},
        {'name':'Порог','value':'I квартал без порога; II — I+II; III — III; IV и год — весь год; неположительный план не определяет выполнение'}]
    parameters.append({'name':'В годовую за квартал','value':'Начисляется до квартальных условий выплаты с поправкой годового периода. Не прибавляется к квартальной выплате. Пустые поля в старом снимке требуют нового сравнения.'})
    for case in data['scenarios']:parameters.append({'name':f"Вариант {case['id']}",'value':SCENARIO_DESCRIPTIONS[case['id']]})
    for year,source in data['sources'].items():
        parameters.append({'name':f'Источник {year}','value':f"{source.get('source','Исходный расчёт')} · {source['run_id']} · {source['completed_at']} · план {data['plans'][year]['source_version']}"})
        for item in source.get('discount_segments',[]):
            parameters.append({'name':f"{year}: сегменты {item['type_discount']}",'value':'; '.join(f'{k}={v}' for k,v in item.items() if k!='type_discount')})
        for key,value in source['parameters'].items():parameters.append({'name':f'{year}: {key}','value':str(value)})
    table(book,'Параметры',[('name','Параметр'),('value','Значение')],parameters,freeze_panes='A2')
    book['Параметры'].column_dimensions['B'].width=110
    output=BytesIO();book.save(output);output.seek(0);return output
