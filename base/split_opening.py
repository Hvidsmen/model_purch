"""Opening inventory is independent of plan sales, prices and future receipts."""
from .split_engine import ZERO,forecast_months,number,precise
from .split_preplan_forecast import resolve


@precise
def opening_balance(scenario,group):
    powers=group.get('powers',{});months=forecast_months(scenario)
    resolved=resolve(scenario,group);issues=resolved['issues']
    predictions=[resolved['powers'][key] for key in powers]
    rows=[]
    for (key,power),predicted in zip(powers.items(),predictions):
        row={'power':key,'forecast':predicted}
        for kind in ('indoor','outdoor'):
            initial=number(power.get('stock_'+kind),optional=True)
            prior=number(power.get('prior_'+kind),optional=True) if months else ZERO
            row['stock_'+kind]=initial;row['prior_'+kind]=prior
            row['opening_'+kind]=None if initial is None or prior is None or predicted is None else initial+prior-predicted
        row['sets']=None if row['opening_indoor'] is None or row['opening_outdoor'] is None else min(row['opening_indoor'],row['opening_outdoor'])
        row['deficit']=row['sets'] is not None and row['sets']<0
        rows.append(row)
    totals={}
    for field in ('stock_indoor','stock_outdoor','prior_indoor','prior_outdoor','forecast','opening_indoor','opening_outdoor','sets'):
        values=[row[field] for row in rows]
        totals[field]=None if not values or any(value is None for value in values) else sum(values,ZERO)
    return {'rows':rows,'totals':totals,'forecast_total':resolved['total'],'issues':issues,'deficit':any(row['deficit'] for row in rows)}
