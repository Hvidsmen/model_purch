"""One forecast resolver shared by opening and ending inventory."""
from calendar import monthrange
from datetime import date,timedelta
from django.core.exceptions import ValidationError
from .split_engine import ZERO,HUNDRED,allocate,forecast_months,number,precise


@precise
def resolve(scenario,group):
    powers=group.get('powers',{});keys=list(powers);months=forecast_months(scenario)
    total=None;values={key:None for key in keys};issues=[]
    if not months:return {'total':ZERO,'powers':{key:ZERO for key in keys},'issues':[]}
    mode=group.get('forecast_mode','monthly')
    try:
        if mode=='powers':
            values={key:number(power.get('forecast_before'),optional=True) for key,power in powers.items()}
            if any(value is None for value in values.values()):
                issues.append('Заполните прогноз каждой мощности; отсутствие продаж укажите нулём.')
            else:total=sum(values.values(),ZERO)
        else:
            if mode=='group':total=number(group.get('forecast_before_total'),optional=True)
            elif mode=='monthly':
                monthly=[number(group.get('forecast',{}).get(month),optional=True) for month in months]
                if any(value is None for value in monthly):raise ValidationError('Заполните прогноз продаж до начала плана; отсутствие продаж укажите нулём.')
                for month,imported in group.get('forecast_imports',{}).items():
                    first=date.fromisoformat(month+'-01');last=first.replace(day=monthrange(first.year,first.month)[1])
                    start=max(first,scenario.stock_date+timedelta(days=1));end=min(last,scenario.start-timedelta(days=1))
                    if date.fromisoformat(imported['start'])>start or date.fromisoformat(imported['end'])<end:
                        raise ValidationError('Перенесён только фрагмент прогноза. Дополните и сохраните месячный прогноз вручную.')
                total=sum(monthly,ZERO)
            else:raise ValidationError('Неизвестный способ ввода прогноза.')
            if total is None:raise ValidationError('Введите общий прогноз группы до начала плана.')
            if total==0:values={key:ZERO for key in keys}
            else:
                shares=[number(power.get('share'),optional=True) for power in powers.values()]
                if not shares or any(value is None for value in shares) or sum(shares,ZERO)!=HUNDRED:
                    raise ValidationError('Для общего прогноза нужны доли мощностей 100%. Либо введите прогноз прямо по мощностям.')
                values=dict(zip(keys,allocate(total,shares)))
    except ValidationError as exc:issues.extend(exc.messages)
    return {'total':total,'powers':values,'issues':issues}
