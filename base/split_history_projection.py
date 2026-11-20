"""Read-only previous-year fact plus the saved pre-plan forecast, without overlap."""
from calendar import monthrange
from copy import deepcopy
from datetime import date,timedelta
from decimal import Decimal,ROUND_HALF_UP
from django.core.exceptions import ValidationError
from django.utils import timezone
from zoneinfo import ZoneInfo
from .split_history import presentation as raw_history
from .split_engine import ZERO,STEP,allocate,number,precise
from .split_preplan_forecast import resolve
from .split_matrix_totals import aggregate


@precise
def presentation(scenario,enabled=True):
    history=raw_history(scenario);year=scenario.start.year-1
    info={'active':False,'year':year,'issues':[],'note':''}
    if not enabled or not history['snapshot']:return history,info
    if scenario.start!=date(year+1,1,1) or scenario.stock_date.year!=year or scenario.stock_date>timezone.localdate(timezone=ZoneInfo('Europe/Moscow')):
        info['note']='Прогноз не добавлен: нужен завершившийся срез стока предыдущего года и начало плана 1 января.'
        return history,info
    snapshot=history['snapshot'];aligned=snapshot.get('projection_snapshot')
    if snapshot.get('through')==str(scenario.stock_date):aligned=snapshot
    if not aligned or aligned.get('through')!=str(scenario.stock_date):
        info['note']=('Прогноз не добавлен: в утверждённом снимке нет факта на дату стока. Для обновления создайте корректировку.' if scenario.status=='approved' else 'Прогноз не добавлен: нажмите «Загрузить факт», чтобы получить отдельный срез на дату стока без пересечения с прогнозом.')
        return history,info
    draft=deepcopy(scenario);draft.state['history']=aligned
    history=raw_history(draft);history['projection_year']=year;info['active']=True
    info['note']='Факт по '+scenario.stock_date.strftime('%d.%m.%Y')+' + прогноз шага 4 до конца года. Количество — комплекты. Прогноз по каналам — пропорционально фактическим комплектам; деньги — по средней себестоимости и выручке факта. Прогноз группы/мощностей по месяцам — пропорционально календарным дням.'
    first=scenario.stock_date+timedelta(days=1);last=date(year,12,31)
    for group_row in history['groups']:
        group=scenario.state['groups'][group_row['gid']]
        annual=next(item for item in group_row['years'] if item['year']==year)
        original=deepcopy(annual['annual']);reason=''
        try:
            forecast=resolve(scenario,group)
            if forecast['issues'] or forecast['total'] is None:raise ValidationError('Нужен заполненный прогноз шага 4. '+' '.join(forecast['issues']))
            total=forecast['total']
            weights=[max(cell['qty'],ZERO) if cell['qty'] is not None else None for cell in original]
            if any(value is None for value in weights):raise ValidationError('Не хватает факта по каналам для распределения прогноза.')
            if total and not sum(weights,ZERO):raise ValidationError('Нет положительного факта для распределения прогноза между каналами.')
            monthly=[]
            for month in range(1,13):
                start=max(first,date(year,month,1));end=date(year,month,monthrange(year,month)[1])
                monthly.append(max(0,(end-start).days+1))
            if group.get('forecast_mode','monthly')=='monthly' and first<=last:
                quantities=[number(group.get('forecast',{}).get(f'{year}-{month:02}')) if days else ZERO for month,days in enumerate(monthly,1)]
            else:quantities=allocate(total,list(map(Decimal,monthly))) if total else [ZERO]*12
            for index,month in enumerate(annual['months']):
                extra=allocate(quantities[index],weights) if quantities[index] else [ZERO]*len(weights)
                for channel,cell in enumerate(month['cells']):
                    # Future months have known zero actual; current month contains only the cut-off slice.
                    if date(year,index+1,1)>scenario.stock_date:
                        cell.update(qty=ZERO,revenue=ZERO,cost=ZERO,markup=None)
                    addition=extra[channel]
                    cell['qty']=None if cell['qty'] is None else cell['qty']+addition
                    for field in ('revenue','cost'):
                        actual=original[channel][field];qty=original[channel]['qty']
                        amount=(addition*actual/qty).quantize(STEP,rounding=ROUND_HALF_UP) if addition and actual is not None and qty and actual>=0 else ZERO if not addition else None
                        cell[field]=cell[field]+amount if cell[field] is not None and amount is not None else None
                    cell['markup']=cell['revenue']/cell['cost'] if cell['revenue'] is not None and cell['cost'] else None
            annual['annual']=[{**aggregate(month['cells'][index] for month in annual['months']),
                               'cid':cell['cid'],'channel':cell['channel']} for index,cell in enumerate(original)]
        except ValidationError as exc:reason=' '.join(exc.messages)
        if reason:
            info['issues'].append({'name':group['ref']['name'],'reason':reason})
            for cell in annual['annual']:cell.update(qty=None,revenue=None,cost=None,markup=None,projection_note='Факт + прогноз не рассчитан')
            for month in annual['months']:
                if date.fromisoformat(month['month']+'-01')>scenario.stock_date:
                    for cell in month['cells']:cell.update(qty=None,revenue=None,cost=None,markup=None)
    return history,info
