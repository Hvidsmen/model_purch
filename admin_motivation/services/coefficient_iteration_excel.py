"""Styled per-scenario results with collapsed possible/annual-quarter columns."""
from io import BytesIO
from decimal import Decimal,InvalidOperation
from openpyxl import Workbook
from openpyxl.styles import Font,PatternFill,Alignment,Border,Side
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.utils import get_column_letter
from .coefficient_iteration_runner import aggregate_rows


def text(cell,value):
    cell.value=ILLEGAL_CHARACTERS_RE.sub('',str(value))[:32767];cell.data_type='s'


def table(book,title,headers,rows,text_columns=()):
    sheet=book.create_sheet(title);sheet.sheet_view.showGridLines=False
    sheet.append(headers);sheet.row_dimensions[1].height=56
    for cell in sheet[1]:
        cell.font=Font(name='Calibri',bold=True,color='FFFFFF');cell.fill=PatternFill('solid',fgColor='6650BE')
        cell.alignment=Alignment(wrap_text=True,vertical='center')
    for values in rows:
        row=sheet.max_row+1
        for index,value in enumerate(values,1):
            cell=sheet.cell(row,index)
            if value is None:pass
            elif index in text_columns:text(cell,value)
            else:
                try:
                    numeric=Decimal(str(value))
                    if not numeric.is_finite():raise InvalidOperation
                    cell.value=numeric;cell.number_format='#,##0.00'
                except (InvalidOperation,ValueError):text(cell,value)
            cell.font=Font(name='Calibri',size=11,color='27364D')
            cell.alignment=Alignment(vertical='center',wrap_text=index<=3)
            cell.border=Border(bottom=Side(style='hair',color='DFE4EF'))
            if row%2==0:cell.fill=PatternFill('solid',fgColor='F5F3FC')
        sheet.row_dimensions[row].height=30
        if values and (str(values[0])=='ИТОГО' or 'ИТОГО' in str(values[1] if len(values)>1 else '')):
            for cell in sheet[row]:cell.font=Font(name='Calibri',bold=True,color='FFFFFF');cell.fill=PatternFill('solid',fgColor='27364D')
    for i in range(1,len(headers)+1):sheet.column_dimensions[get_column_letter(i)].width=34 if i<=3 else 23
    sheet.freeze_panes='D2';sheet.auto_filter.ref=sheet.dimensions
    sheet.page_setup.orientation='landscape';sheet.page_setup.fitToWidth=1;sheet.page_setup.fitToHeight=0
    sheet.sheet_properties.pageSetUpPr.fitToPage=True;sheet.print_title_rows='1:1'
    return sheet


def result_table(book,title,rows,year,names=None):
    quarters=[4] if year==2026 else [1,2,3,4]
    headers=['Подразделение','Вид мотивации','Сотрудник']
    groups=[]
    for q in quarters:
        groups.append((len(headers)+1,len(headers)+1,len(headers)+2))
        headers.extend([f'{q} кв.: премия без условия выплаты, USD',f'{q} кв.: премия с учётом выполнения, USD'])
    headers.append('Сумма квартальных премий, USD')
    first=len(headers)+1
    headers.extend([f'Из {q} кв. в годовую премию, USD' for q in quarters])
    anchor=len(headers)+1;groups.append((first,anchor-1,anchor))
    headers.extend(['В годовую премию до условия выплаты, USD','Годовая часть выплачена, USD','Доплата за кварталы, USD','Общая выплата, USD'])
    values=[]
    for row in rows:
        cells=[(names or {}).get(row['branch'],row['branch']),row['kind'],row.get('employee','')]
        for q in quarters:cells.extend([row['possible'][q-1],row['quarters'][q-1]])
        cells.append(sum((Decimal(str(value)) for value in row['quarters']),Decimal(0)))
        cells.extend(row['annual_quarters'][q-1] for q in quarters)
        cells.extend(row[field] for field in ('annual_accrued','annual_paid','bonus','total'));values.append(cells)
    sheet=table(book,title,headers,values,text_columns=(1,2,3))
    sheet.sheet_properties.outlinePr.summaryRight=True;sheet.sheet_view.showOutlineSymbols=True
    for first,last,anchor in groups:
        sheet.column_dimensions.group(get_column_letter(first),get_column_letter(last),outline_level=1,hidden=True)
        sheet.column_dimensions[get_column_letter(anchor)].collapsed=True
    return sheet


def workbook(run):
    book=Workbook();book.remove(book.active)
    names={row['branch']:row.get('branch_name',row['branch']) for plan in run.inputs.get('plans',{}).values() for row in plan['rows']}
    from django.utils import timezone
    from zoneinfo import ZoneInfo
    local=lambda value:timezone.localtime(value,ZoneInfo('Europe/Moscow')).isoformat() if value else ''
    meta=[['Итерация',run.iteration_id],['Запуск',run.pk],['Состояние',run.state],['Правило',run.inputs.get('rule','')],
          ['Начало, МСК',local(run.started_at)],['Завершение, МСК',local(run.completed_at)],['Ошибка',run.error],
          ['Квартальная доля, %',run.configuration.get('quarter_share')],['Годовая доля, %',run.configuration.get('year_share')],
          ['Порог выплаты',run.configuration.get('payout_threshold')],
          ['Исключённые SubdivisionName',', '.join(run.inputs.get('excluded_subdivision_names',[]))],
          ['Правило участия подразделений',run.inputs.get('subdivision_policy','')],
          ['Примечание','Годовая часть не включена в сумму квартальных. Общая выплата = квартальные + годовая выплата + доплата.'],
          ['2026','Москва + ТТ; только IV квартал и годовая часть из IV квартала; факт = план.'],
          ['2027','Четыре квартала и годовая часть из всех кварталов; факт = план; политики общим планом.'],
          ['2025','Реальный подготовленный факт; Москва и ТТ раздельно; ТТ по ставкам Москвы; исторические директора.']]
    meta.extend([['Ревизия сопоставления',run.inputs.get('revision')],['Версия формул директоров',run.inputs.get('formulas',{}).get('version_name','')]])
    for source in ('branches','directors'):
        upload=run.inputs.get('payload',{}).get(source,{})
        meta.extend([[source+': файл',upload.get('name','')],[source+': SHA256',upload.get('sha256','')]])
    for year,plan in run.inputs.get('plans',{}).items():
        for field in ('title','source','version','loaded_at','sha256'):meta.append([f'План {year}: {field}',str(plan.get(field,''))])
    for scenario,result in run.results.items():
        if result.get('state')!='completed':meta.append([result.get('title',scenario),result.get('error','Расчёт не завершён')]);continue
        year=result['year'];name='IV кв 2026' if year==2026 else str(year)
        result_table(book,'Сводка '+name,aggregate_rows(result['rows']),year,names)
        result_table(book,'Получатели '+name,result['rows'],year,names)
        if result.get('source'):meta.append([f'Источник {year}',str(result['source'])])
        details=result.get('details',[])
        if details:
            headers=['Подразделение','Сотрудник','Вид мотивации','Исходное подразделение','Период','Группа планов','Группа ERP','Бренд','Формула','План USD','Факт USD','Выполнение','Возможная квартальная USD','Квартальная выплата USD','В годовую USD','Основание']
            table(book,'Расшифровка '+name,headers,[[(names.get(r.get(k),r.get(k)) if k in ('branch','original_branch') else r.get(k)) for k in ('branch','employee','kind','original_branch','date' if year!=2025 else 'period','planning','goods','brand','formula','plan','fact','execution' if year!=2025 else 'ratio','possible','paid','annual_contribution','origin' if year!=2025 else 'note')] for r in details],text_columns=(1,2,3,4,5,6,7,8,9,16))
    receipts=[]
    for year,plan in run.inputs.get('plans',{}).items():
        for row in plan['rows']:
            receipts.append([year,row.get('date'),row['branch'],row.get('branch_name',row['branch']),
                row.get('channel',''),row.get('planning'),row.get('goods'),row.get('brand'),row.get('amount'),
                sum((Decimal(str(v)) for v in row.get('policy_segments',row.get('segments',[]))),Decimal(0)),*row.get('segments',[None]*5)])
    table(book,'Источники плана',['Год','Дата','SubdivisionKey','SubdivisionName','Канал','Группа планов','Группа ERP','Бренд','План USD','База политиков USD','O0','O1','O2','O3','O4'],receipts,text_columns=range(1,9))
    table(book,'Подразделения',['SubdivisionKey','Название'],sorted(names.items()),text_columns=(1,2))
    parameters=table(book,'Параметры',['Параметр','Значение'],meta,text_columns=(1,2));parameters.column_dimensions['B'].width=110
    from .coefficient_iteration import mapping_rows
    payload=run.inputs.get('payload');mappings=run.inputs.get('mappings',{})
    if payload:
        values=[];people=run.inputs.get('people',{});subs=run.inputs.get('subdivision_names',run.inputs.get('subdivisions',{}))
        for row in mapping_rows(payload):
            mapping=mappings.get(row['id'],{});block=payload[row['source']]['blocks'][row['block']]
            targets=[mapping.get('subdivision')] if row['source']=='branches' else mapping.get('subdivisions',[])
            for entry in block['entries']:
                values.append([row['kind'],row['label'],', '.join(str(subs.get(str(i),'')) for i in targets),
                    people.get(str(mapping.get('employee')),''),people.get(str(mapping.get('employee_2025')),''),
                    *entry['key'],*entry['rates']])
        sheet=table(book,'Снимок ставок',['Вид','Название в файле','Привязанные подразделения','Директор 2026–2027','Директор 2025','Группа планов','Группа ERP','Бренд','K0','K1','K2','K3','K4'],values,text_columns=range(1,9))
        for row in sheet.iter_rows(min_row=2,min_col=9,max_col=13):
            for cell in row:cell.number_format='0.0000%'
    output=BytesIO();book.save(output);book.close();output.seek(0);return output
