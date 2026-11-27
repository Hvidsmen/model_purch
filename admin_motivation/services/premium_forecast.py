"""Indicative 100%-of-plan premium at the current sales mix and effective rates."""
from decimal import Decimal,InvalidOperation
from .reports_next import period_code

ZERO=Decimal(0)
NOTE='Оценка при прежней структуре продаж, скидках, ставках и поправках: начисление × план / факт по подразделению, виду и периоду. Годовая часть — до годового условия выплаты. Доплаты за прошлые кварталы не включены.'


def number(value):
    try:
        result=Decimal(str(value))
        return result if result.is_finite() else None
    except (InvalidOperation,TypeError,ValueError):return None


def total(values):
    values=list(values)
    return sum(values,ZERO) if values and all(value is not None for value in values) else None


def enrich(display,ledger):
    if not ledger.get('shareholder_forecast') or not ledger.get('rows'):return
    bases={}
    for row in ledger.get('coefficient_plan_fact',[]):
        code=period_code(row.get('period',''))
        if code not in ('1','2','3','4','5'):continue
        key=(row.get('subdivision'),row.get('kind'),code)
        # Distinct years or duplicate bases must never silently overwrite.
        bases.setdefault(key,[]).append(row)
    items=[];by_source={}
    for row in ledger['rows']:
        code=row['code'];base=bases.get((row['subdivision'],row['kind'],code),[])
        factor=None
        if len(base)==1 and code!='1':
            plan,fact=number(base[0].get('plan')),number(base[0].get('fact'))
            if plan is not None and plan>0 and fact is not None and fact>0:factor=plan/fact
        possible=number(row.get('possible'));annual=number(row.get('annual_accrual'))
        quarter=possible*factor if code!='5' and possible is not None and factor is not None else (ZERO if code=='5' else None)
        annual=(possible*factor if possible is not None and factor is not None else None) if code=='5' else (annual*factor if annual is not None and factor is not None else None)
        item=dict(kind=row['kind'],code=code,quarter=quarter,annual=annual)
        items.append(item);by_source[id(row)]=item
    def summary(selected):
        selected=list(selected)
        quarters=[row for row in selected if row['code']!='5']
        year=[row for row in selected if row['code']=='5']
        return dict(quarter=total(r['quarter'] for r in quarters) if quarters else ZERO,
                    annual=total(r['annual'] for r in (year or quarters)),
                    kinds=[dict(kind=kind,quarter=total(r['quarter'] for r in quarters if r['kind']==kind) if quarters else ZERO,
                                annual=total(r['annual'] for r in (year or quarters) if r['kind']==kind))
                           for kind in dict.fromkeys(r['kind'] for r in selected)])
    display['forecast_100']=summary(items)
    display['forecast_100_note']=NOTE
    for card in display['period_cards']:
        card['forecast_100']=summary(row for row in items if row['code']==card['code'])

    for group in [*display.get('employees',[]),*display.get('registry_rows',[])]:
        selected=[by_source[id(row)] for row in group['rows']]
        group['forecast_100']=summary(selected)
        for column,cell in zip(display.get('period_columns',[]),group.get('period_cells',[])):
            period_items=[row for row in selected if row['code']==column['code']]
            if period_items:cell['forecast_100']=summary(period_items)
    for section in display.get('team_sections',[]):
        selected=[row for row in items if row['kind']==section['kind']]
        section['forecast_100']=summary(selected)
        for column,cell in zip(display.get('period_columns',[]),section.get('period_summary_cells',[])):
            period_items=[row for row in selected if row['code']==column['code']]
            if period_items:cell['forecast_100']=summary(period_items)
