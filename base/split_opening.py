"""Opening inventory is independent of plan sales, prices and future receipts."""
from calendar import monthrange
from datetime import date,timedelta
from django.core.exceptions import ValidationError
from .split_engine import ZERO,HUNDRED,allocate,forecast_months,number,precise


@precise
def opening_balance(scenario,group):
    powers=group.get('powers',{});months=forecast_months(scenario);issues=[]
    forecast=None;predictions=[None]*len(powers)
    try:
        values=[number(group.get('forecast',{}).get(month),optional=True) for month in months]
        if any(value is None for value in values):raise ValidationError('Заполните прогноз продаж до начала плана; отсутствие продаж укажите нулём.')
        for month,imported in group.get('forecast_imports',{}).items():
            first=date.fromisoformat(month+'-01');last=first.replace(day=monthrange(first.year,first.month)[1])
            start=max(first,scenario.stock_date+timedelta(days=1));end=min(last,scenario.start-timedelta(days=1))
            if date.fromisoformat(imported['start'])>start or date.fromisoformat(imported['end'])<end:
                raise ValidationError('Дополните частично загруженный прогноз и сохраните его.')
        forecast=sum(values,ZERO)
        if forecast==0:predictions=[ZERO]*len(powers)
        else:
            shares=[number(power.get('share'),optional=True) for power in powers.values()]
            if not shares or any(value is None for value in shares) or sum(shares,ZERO)!=HUNDRED:
                raise ValidationError('Для распределения прогноза заполните доли мощностей: сумма должна быть 100%.')
            predictions=allocate(forecast,shares)
    except ValidationError as exc:issues.extend(exc.messages)
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
    return {'rows':rows,'totals':totals,'issues':issues,'deficit':any(row['deficit'] for row in rows)}
