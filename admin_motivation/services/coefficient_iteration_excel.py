"""Styled per-scenario results with collapsed possible/annual-quarter columns."""
from tempfile import TemporaryFile
from decimal import Decimal,InvalidOperation
from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell
from openpyxl.styles import Font,PatternFill,Alignment,Border,Side
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.utils import get_column_letter
from .coefficient_iteration_runner import aggregate_rows


def text(cell,value):
    cell.value=ILLEGAL_CHARACTERS_RE.sub('',str(value))[:32767];cell.data_type='s'


BODY_FONT=Font(name='Calibri',size=11,color='27364D')
HEADER_FONT=Font(name='Calibri',bold=True,color='FFFFFF')
TOTAL_FONT=Font(name='Calibri',size=11,bold=True,color='FFFFFF')
HEADER_FILL=PatternFill('solid',fgColor='6650BE')
TOTAL_FILL=PatternFill('solid',fgColor='27364D')
STRIPE_FILL=PatternFill('solid',fgColor='F5F3FC')
WRAP=Alignment(wrap_text=True,vertical='center')
PLAIN=Alignment(vertical='center')
BODY_BORDER=Border(bottom=Side(style='hair',color='DFE4EF'))


def table(book,title,headers,rows,text_columns=(),*,column_groups=(),percent_columns=(),widths=None):
    sheet=book.create_sheet(title);sheet.sheet_view.showGridLines=False
    sheet.sheet_format.defaultRowHeight=30;sheet.row_dimensions[1].height=56
    sheet.freeze_panes='D2'
    for i in range(1,len(headers)+1):sheet.column_dimensions[get_column_letter(i)].width=34 if i<=3 else 23
    for column,width in (widths or {}).items():sheet.column_dimensions[column].width=width
    if column_groups:
        sheet.sheet_properties.outlinePr.summaryRight=True;sheet.sheet_view.showOutlineSymbols=True
        for first,last,anchor in column_groups:
            sheet.column_dimensions.group(get_column_letter(first),get_column_letter(last),outline_level=1,hidden=True)
            sheet.column_dimensions[get_column_letter(anchor)].collapsed=True
    sheet.page_setup.orientation='landscape';sheet.page_setup.fitToWidth=1;sheet.page_setup.fitToHeight=0
    sheet.sheet_properties.pageSetUpPr.fitToPage=True;sheet.print_title_rows='1:1'
    heading=[]
    for value in headers:
        cell=WriteOnlyCell(sheet);text(cell,value)
        cell.font=HEADER_FONT;cell.fill=HEADER_FILL;cell.alignment=WRAP;heading.append(cell)
    sheet.append(heading)
    last_row=1
    for row,values in enumerate(rows,2):
        last_row=row;cells=[]
        total=bool(values and (str(values[0])=='ИТОГО' or 'ИТОГО' in str(values[1] if len(values)>1 else '')))
        for index,value in enumerate(values,1):
            cell=WriteOnlyCell(sheet)
            if value is None:pass
            elif index in text_columns:text(cell,value)
            else:
                try:
                    numeric=Decimal(str(value))
                    if not numeric.is_finite():raise InvalidOperation
                    cell.value=numeric;cell.number_format='0.0000%' if index in percent_columns else '#,##0.00'
                except (InvalidOperation,ValueError):text(cell,value)
            cell.font=TOTAL_FONT if total else BODY_FONT
            cell.alignment=WRAP if index<=3 else PLAIN;cell.border=BODY_BORDER
            if total:cell.fill=TOTAL_FILL
            elif row%2==0:cell.fill=STRIPE_FILL
            cells.append(cell)
        sheet.append(cells)
    sheet.auto_filter.ref=f'A1:{get_column_letter(len(headers))}{last_row}'
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
    def values():
        for row in rows:
            cells=[(names or {}).get(row['branch'],row['branch']),row['kind'],row.get('employee','')]
            for q in quarters:cells.extend([row['possible'][q-1],row['quarters'][q-1]])
            cells.append(sum((Decimal(str(value)) for value in row['quarters']),Decimal(0)))
            cells.extend(row['annual_quarters'][q-1] for q in quarters)
            cells.extend(row[field] for field in ('annual_accrued','annual_paid','bonus','total'))
            yield cells
    return table(book,title,headers,values(),text_columns=(1,2,3),column_groups=groups)


def workbook(run):
    book=Workbook(write_only=True)
    names=run.inputs.get('export_names',{})
    from django.utils import timezone
    from zoneinfo import ZoneInfo
    local=lambda value:timezone.localtime(value,ZoneInfo('Europe/Moscow')).isoformat() if value else ''
    meta=[['Итерация',run.iteration_id],['Запуск',run.pk],['Состояние',run.state],['Правило',run.inputs.get('rule','')],
          ['Начало, МСК',local(run.started_at)],['Завершение, МСК',local(run.completed_at)],['Ошибка',run.error],
          ['Квартальная доля, %',run.configuration.get('quarter_share')],['Годовая доля, %',run.configuration.get('year_share')],
          ['Порог выплаты',run.configuration.get('payout_threshold')],
          ['Исключённые SubdivisionName',', '.join((run.inputs.get('excluded_subdivision_names') or []))],
          ['Правило участия подразделений',run.inputs.get('subdivision_policy','')],
          ['Исключения групп и каналов',str(run.inputs.get('coefficient_exclusions',[]))],
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
        source=result.get('source') or {}
        if isinstance(source,dict):
            for field in ('source','prepared_run_id','completed_at'):
                if source.get(field) is not None:meta.append([f'Источник {year}: {field}',str(source[field])])
        def bases():
            for row in result.get('plan_fact_summary') or []:
                yield [names.get(row['branch'],row['branch']),row['kind'],row['period'],
                       row.get('plan'),row.get('fact'),row.get('execution'),
                       *(row.get('segments') or [None]*5),row.get('note','')]
        table(book,'План-факт '+name,
              ['Подразделение','Вид мотивации','Период','План, USD','Факт, USD','Выполнение',
               'O0, USD','O1, USD','O2, USD','O3, USD','O4, USD','Примечание'],
              bases(),text_columns=(1,2,3,12),percent_columns=(6,),widths={'L':80})
    table(book,'Параметры',['Параметр','Значение'],meta,text_columns=(1,2),widths={'B':110})
    output=TemporaryFile()
    try:
        book.save(output)
    except Exception:
        output.close();raise
    finally:
        book.close()
    output.seek(0);return output
