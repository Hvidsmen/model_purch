"""Quarter-column execution export; department bases are never summed windows."""
from collections import defaultdict
from decimal import Decimal
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

PERIODS=('1 квартал','2 квартал (1–2 кв.)','3 квартал','4 квартал (1–4 кв.)','Год')
METRICS=(('plan','План проверки, USD'),('fact','Факт проверки, USD'),
 ('ratio','Выполнение по формуле, %'),('threshold','Порог выплаты, %'),
 ('department_ratio','Выполнение подразделения, %'),('author_ratio','Выполнение сотрудника, %'),
 ('possible','Начислено, USD'),('paid','К выплате, USD'),('bonus','Доплаты, USD'),
 ('total','Итого к выплате, USD'),('reason','Пояснение'))


def period_code(value):
    value=str(value).strip()
    return 5 if value.casefold()=='год' else int(value[0])


def append_sheets(book,sections):
    for section in sections:
        policies=section['kind']=='Политики'
        sheet=book.create_sheet('3. Выполнение политик' if policies else '4. Выполнение продаж')
        dimensions=['Подразделение']+(['Сотрудник'] if policies else [])+['Группа планов продаж','Группа ERP','Бренд','Формула']
        start=len(dimensions)+1
        width=len(dimensions)+len(PERIODS)*len(METRICS)
        sheet.append(['Выполнение плана мотивации · 2025 · USD'])
        sheet.append(['Итог подразделения: план и факт взяты один раз из базы подразделения; проценты не суммируются. '
            'Детали содержат базы проверки формул, которые могут повторяться. I квартал: план проверки равен факту, порог не применяется. '
            'II квартал проверяет I–II, IV и год — весь год. Раскройте детали кнопкой +.'])
        for row in (1,2):sheet.merge_cells(start_row=row,start_column=1,end_row=row,end_column=width)
        for column,label in enumerate(dimensions,1):
            sheet.cell(3,column,label);sheet.merge_cells(start_row=3,start_column=column,end_row=4,end_column=column)
        for period,label in enumerate(PERIODS):
            first=start+period*len(METRICS)
            sheet.cell(3,first,label)
            sheet.merge_cells(start_row=3,start_column=first,end_row=3,end_column=first+len(METRICS)-1)
            for offset,(_,metric) in enumerate(METRICS):sheet.cell(4,first+offset,metric)
        branches=defaultdict(list)
        for row in section.get('performance',[]):branches[row['branch']].append(row)
        for branch,rows in sorted(branches.items()):
            totals={}
            for period in range(1,6):
                items=[r for r in rows if period_code(r['period'])==period]
                if not items:continue
                # These source bases repeat on every detail row. Read once.
                bases={(r.get('department_plan'),r.get('department_fact')) for r in items}
                if len(bases)!=1:raise ValueError('Несогласованные базы выполнения подразделения')
                plan,fact=next(iter(bases))
                if period==1:plan=fact
                total={'plan':plan,'fact':fact,'ratio':fact/plan if plan is not None and plan>0 else None,
                    'threshold':None,'department_ratio':None,'author_ratio':None,
                    'reason':'Итог подразделения; условия выплаты — в деталях'}
                for key in ('possible','paid','bonus','total'):total[key]=sum((r[key] for r in items),Decimal(0))
                totals[period]=total
            def append(values,periods):
                sheet.append(values+[periods.get(period,{}).get(key) for period in range(1,6) for key,_ in METRICS])
            append([branch]+(['Итого по подразделению'] if policies else [])+(['']*4),totals)
            subtotal=sheet.max_row
            for cell in sheet[subtotal]:
                cell.font=Font(bold=True,color='243B53');cell.fill=PatternFill('solid',fgColor='F1EDFF')
            details={};occurrences=defaultdict(int)
            for row in rows:
                key=tuple(row[k] for k in (('author',) if policies else ())+('planning','goods','brand','formula'))
                period=period_code(row['period'])
                occurrence=occurrences[key,period];occurrences[key,period]+=1
                details.setdefault((key,occurrence),{})[period]=row
            for (key,_),periods in details.items():append([branch,*key],periods)
            if sheet.max_row>subtotal:
                sheet.row_dimensions.group(subtotal+1,sheet.max_row,outline_level=1,hidden=True)
                sheet.row_dimensions[subtotal].collapsed=True
        sheet.sheet_properties.outlinePr.summaryBelow=False
        sheet.sheet_view.showOutlineSymbols=True
        sheet.sheet_format.outlineLevelRow=1
        for row in (3,4):
            for cell in sheet[row]:
                cell.font=Font(bold=True,color='243B53');cell.fill=PatternFill('solid',fgColor='EDE9FE')
                cell.alignment=Alignment(wrap_text=True,vertical='center')
        sheet.row_dimensions[2].height=50
        sheet.cell(2,1).alignment=Alignment(wrap_text=True,vertical='center')
        sheet.row_dimensions[3].height=25;sheet.row_dimensions[4].height=60
        for column in range(1,width+1):
            metric=METRICS[(column-start)%len(METRICS)][1] if column>=start else ''
            sheet.column_dimensions[get_column_letter(column)].width=60 if metric=='Пояснение' else 25
            for row in range(5,sheet.max_row+1):
                cell=sheet.cell(row,column)
                if isinstance(cell.value,str):cell.data_type='s'
                if metric.endswith('%'):cell.number_format='0.00%'
                elif metric.endswith('USD'):cell.number_format='#,##0.00 "USD"'
                cell.alignment=Alignment(vertical='top',wrap_text=isinstance(cell.value,str))
        sheet.freeze_panes=f'{get_column_letter(start)}5'
