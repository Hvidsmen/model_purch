"""Present plan-only estimates; never extrapolate current actual premiums."""
from collections import defaultdict
from decimal import Decimal,InvalidOperation

ZERO=Decimal(0)
from .plan_premium_forecast import NOTE


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
    from .plan_premium_forecast import empty_plan_known
    forecast_index=defaultdict(list)
    for item in ledger.get('plan_forecast_rows',[]):
        forecast_index[(item.get('Подразделение'),item.get('АвторСделки') or '—',item['kind'],item['code'])].append(item)
    items=[];by_source={}
    for row in ledger['rows']:
        selected=forecast_index.get((row['subdivision'],row['author'],row['kind'],row['code']),[])
        if not selected and empty_plan_known(ledger,row['kind']):selected=[{'quarter':ZERO,'annual':ZERO}]
        item=dict(kind=row['kind'],code=row['code'],
                  quarter=ZERO if row['code']=='5' else total(r['quarter'] for r in selected),
                  annual=total(r['annual'] for r in selected))
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
    if not ledger.get('plan_forecast_available'):
        display['forecast_100_note']+=' В этом снимке нет данных для нового прогноза. Создайте новый акционерный расчёт.'
    elif ledger.get('currency')=='RUB' and not ledger.get('forecast_usd_rub'):
        display['forecast_100_note']+=' Плановый курс USD/RUB не задан; прогноз в RUB недоступен.'
    sources=sorted({r['forecast_scope'] for r in ledger.get('plan_forecast_rows',[])})
    if sources:display['forecast_100_note']+=' Использована история: '+', '.join(sources)+'.'
    if ledger.get('forecast_usd_rub'):display['forecast_100_note']+=' Плановый курс USD/RUB: '+str(ledger['forecast_usd_rub'])+'.'
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
