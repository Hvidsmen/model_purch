"""Frozen plan-only estimates using positive 2025 sales to distribute K0–K4."""
from collections import defaultdict
from decimal import Decimal, InvalidOperation
import re
from django.core.exceptions import ValidationError

VERSION='plan-2025-segments-v1'
ZERO=Decimal(0)
DIMENSIONS=('Подразделение','АвторСделки','ГруппаПлановПродаж','ГруппаТоваров','Марка')
PRODUCT=('ГруппаПлановПродаж','ГруппаТоваров','Марка')
NOTE='Прогноз = сумма по K0–K4: план × доля ступени по продажам 2025 года × ставка. История: товар → подразделение → компания. Квартальная и годовая части разделены по настройкам расчёта. Факт текущего года и начисленные премии не используются. RUB — по сохранённому плановому курсу.'

HISTORY_SQL="""
WITH sales AS (
 SELECT sub.SubdivisionName AS [Подразделение],p.PlanningGroupSalesERP AS [ГруппаПлановПродаж],
 DataWH.motivation.fn_ReportGroupERP(p.PlanningGroupSalesERP,p.GroupERP) AS [ГруппаТоваров],
 DataWH.motivation.fn_ReportBrand(p.GroupERP,p.BrandName) AS [Марка],
 CASE WHEN s.DiscountMotivation>=COALESCE(sm.O4,10000) THEN 4
 WHEN s.DiscountMotivation>=COALESCE(sm.O3,10000) THEN 3
 WHEN s.DiscountMotivation>=COALESCE(sm.O2,10000) THEN 2
 WHEN s.DiscountMotivation>=COALESCE(sm.O1,10000) THEN 1 ELSE 0 END AS Segment,
 s.Price-s.AmountUSDIt_ITW+s.BallsOuterUSD AS USD
 FROM DataWH.dbo.Sales s
 JOIN DataWH.dbo.Subdivisions sub ON sub.SubdivisionKey=s.Subdivision
 JOIN DataWH.dbo.Product p ON p.ProductCode=s.ProductCode AND p.[Тип номенклатуры]=N'Товар'
 OUTER APPLY (SELECT TOP (1) m.O1,m.O2,m.O3,m.O4
 FROM DataWH.motivation.vSegmentMotivationFromTO m
 WHERE s.ClosedDealDate BETWEEN m.DateFrom AND m.DateTo
 AND s.ДаичиТипСкидкиНаДату LIKE CONCAT('%',m.TypeDiscount,'%')
 ORDER BY LEN(m.TypeDiscount) DESC,m.DateFrom DESC,m.TypeDiscount) sm
 WHERE s.ClosedDealDate>=CONVERT(date,'20250101',112)
 AND s.ClosedDealDate<CONVERT(date,'20260101',112)
 AND EXISTS(SELECT 1 FROM DataWH.dbo.Customer c WHERE c.CustomerKey=s.CustomerKey)
)
SELECT [Подразделение],[ГруппаПлановПродаж],[ГруппаТоваров],[Марка],Segment,SUM(USD) AS USD
FROM sales WHERE USD>0
GROUP BY [Подразделение],[ГруппаПлановПродаж],[ГруппаТоваров],[Марка],Segment
"""


def number(value):
    try:
        result=Decimal(str(value))
        return result if result.is_finite() else None
    except (InvalidOperation,ValueError,TypeError):return None


def plan_sql(batch,kind):
    """Read native dated plan rows before payout projections discard their rates."""
    from .pilot_formula import mask_sql
    match=re.search(r'\bINTO\s+(#plan(?:_\d+)?)\b',mask_sql(batch),re.I)
    if not match:raise ValidationError('Не найдена таблица плана для прогноза премий.')
    if kind not in ('Политики','Продажи','Директора'):raise ValidationError('Неизвестный вид прогноза.')
    fields=','.join('['+name+']' for name in (*DIMENSIONS,'Год','Квартал','ДатаЗакрытияСделки','СуммаПланUSD','VariationCalculate',*[f'K{i}' for i in range(5)]))
    return f"SELECT {fields},LTRIM(RTRIM(TypeCoeff_)) AS [ТипКоэф] FROM {match[1]} WHERE [Год]=2026 AND LTRIM(RTRIM(TypeCoeff_))=N'{kind}'"


class History:
    def __init__(self,rows):
        self.product=defaultdict(lambda:[ZERO]*5)
        self.branch=defaultdict(lambda:[ZERO]*5)
        self.company=[ZERO]*5
        for row in rows:
            amount=number(row.get('USD'))
            try:segment=int(row['Segment'])
            except (ValueError,TypeError,KeyError):raise ValidationError('Повреждена статистика ступеней 2025 года.')
            if amount is None or segment not in range(5):raise ValidationError('Повреждена статистика ступеней 2025 года.')
            if amount<=0:continue
            branch=row.get('Подразделение');product=(branch,*(row.get(k) for k in PRODUCT))
            self.product[product][segment]+=amount;self.branch[branch][segment]+=amount;self.company[segment]+=amount

    def distribution(self,row):
        product=(row.get('Подразделение'),*(row.get(k) for k in PRODUCT))
        for scope,amounts in [('Товар',self.product.get(product,[])),('Подразделение',self.branch.get(product[0],[])),('Компания',self.company)]:
            total=sum(amounts,ZERO)
            if total>0:
                weights=[amount/total for amount in amounts]
                first=next(i for i,v in enumerate(amounts) if v>0)
                weights[first]=Decimal(1)-sum((v for i,v in enumerate(weights) if i!=first),ZERO)
                return weights,scope
        return None,'Нет статистики 2025 года'


def estimate(row,history,quarter_share,year_share,fx=Decimal(1)):
    plan=number(row.get('СуммаПланUSD'));weights,scope=history.distribution(row)
    result={**row,'forecast_scope':scope,'quarter':None,'annual':None,'forecast_parts':[]}
    if plan is None or fx is None or fx<=0:return result
    if plan==0:return {**result,'quarter':ZERO,'annual':ZERO}
    if weights is None:return result
    amounts=[plan*w for w in weights]
    first=next(i for i,w in enumerate(weights) if w>0)
    amounts[first]=plan-sum((v for i,v in enumerate(amounts) if i!=first),ZERO)
    premiums=[];valid=True
    for i,(amount,weight) in enumerate(zip(amounts,weights)):
        rate=number(row.get(f'K{i}'))
        if weight and (rate is None or rate<0):
            premium=None;valid=False
        else:premium=amount*(rate or ZERO)
        premiums.append(premium)
        result['forecast_parts'].append({'step':f'K{i}','weight':weight,'plan':amount,'rate':rate,'premium_usd':premium})
    if not valid:return result
    premium=sum(premiums,ZERO)*fx
    return {**result,'quarter':premium*quarter_share,'annual':premium*year_share}


def load(run,query,periods,kinds):
    """Read immutable forecast snapshots only; no live DWH/coefficients on GET."""
    from .shareholder_reports import matches,KINDS
    from .shareholder_regions import enabled,REGIONS
    if run.metadata.get('plan_forecast_version')!=VERSION:return []
    history_rows=[row for chunk in run.chunks.filter(dataset='forecast_history',mode='combined').only('payload').order_by('sequence') for row in chunk.payload]
    histories={'separate':History(history_rows),'combined':History({**r,'Подразделение':'Д-Москва' if r.get('Подразделение')=='ТЕРМОТРЕЙД' else r.get('Подразделение')} for r in history_rows)}
    quarter_share=number(run.configuration['quarter_share'])/100
    year_share=number(run.configuration['year_share'])/100
    fx=Decimal(1) if query.get('currency','USD')=='USD' else number(run.configuration.get('forecast_usd_rub'))
    search=query.get('search','').strip().casefold();result=[]
    for kind in kinds:
        for chunk in run.chunks.filter(dataset='forecast_plan_'+KINDS[kind]).only('mode','payload').order_by('mode','sequence'):
            for row in chunk.payload:
                if not matches(row,query) or search and search not in str(row.get('АвторСделки') or '').casefold():continue
                for code in periods:
                    if code!='5' and str(row['Квартал'])!=code:continue
                    mode='separate' if run.scenario=='separate' and code in ('1','2','3') else 'combined'
                    if enabled(run) and row.get('Подразделение') in REGIONS:mode='combined'
                    if chunk.mode!=mode:continue
                    item=estimate(row,histories[mode],quarter_share,year_share,fx)
                    result.append({**item,'kind':kind,'code':code,'quarter':ZERO if code=='5' else item['quarter']})
    return result


def empty_plan_known(ledger,kind):
    return (ledger.get('plan_forecast_available') and kind in ('Политики','Продажи','Директора')
            and (ledger.get('currency')=='USD' or (number(ledger.get('forecast_usd_rub')) or ZERO)>0))


def excel_sheet(book,rows):
    """Explain each plan distribution, including the fallback used, without formulas."""
    if not rows:return
    from openpyxl.cell import WriteOnlyCell
    from openpyxl.styles import Alignment,Font,PatternFill
    from openpyxl.utils import get_column_letter
    from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
    sheet=book.create_sheet('Прогноз от плана');sheet.freeze_panes='F2';sheet.sheet_view.showGridLines=False
    headers=['Подразделение','Сотрудник','Вид премии','Период','Дата плана','Группа планов','Группа товаров','Бренд','История 2025','Ступень','Доля ступени','План ступени, USD','Ставка','Премия до разделения, USD']
    def append(values,header=False):
        cells=[]
        for i,value in enumerate(values):
            cell=WriteOnlyCell(sheet,value=ILLEGAL_CHARACTERS_RE.sub('',value)[:32767] if isinstance(value,str) else value)
            if isinstance(value,str):cell.data_type='s'
            cell.font=Font(name='Arial',size=10,color='FFFFFF' if header else '243650',bold=header)
            cell.fill=PatternFill('solid',fgColor='243650' if header else 'F7F8FC')
            cell.alignment=Alignment(vertical='top',wrap_text=True)
            if not header and i in (10,12):cell.number_format='0.00%'
            elif not header and i in (11,13):cell.number_format='#,##0.00'
            cells.append(cell)
        sheet.append(cells)
    for i in range(1,15):sheet.column_dimensions[get_column_letter(i)].width=22 if i!=2 else 34
    append(headers,True)
    count=1
    for row in rows:
        for part in row.get('forecast_parts') or [{}]:
            append([row.get('Подразделение'),row.get('АвторСделки'),row['kind'],'Год' if row['code']=='5' else row['code']+' квартал',row.get('ДатаЗакрытияСделки'),
                    *[row.get(k) for k in PRODUCT],row['forecast_scope'],part.get('step'),part.get('weight'),part.get('plan'),part.get('rate'),part.get('premium_usd')]);count+=1
    sheet.auto_filter.ref='A1:N'+str(count)
