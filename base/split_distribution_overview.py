"""Validate saved distribution and build a source-free overview of the whole plan."""
from decimal import Decimal
from django.core.exceptions import ValidationError
from .split_engine import number, quantity, planning_months, HUNDRED, precise


def values_status(values, target, *, required=True, percentage=True):
    try:
        values = [number(value,optional=True) for value in values]
    except ValidationError:
        return {'key':'invalid','label':'Ошибка','sum':None,'reason':'Некорректные значения'}
    total = sum((value for value in values if value is not None),Decimal(0))
    if not values or any(value is None for value in values):
        return {'key':'missing' if required else 'not_required', 'label':'Не задано' if required else 'Без плана',
                'sum':total if any(value is not None for value in values) else None, 'reason':'Не все доли заполнены' if required else 'Распределение не требуется'}
    if (percentage and any(value > HUNDRED for value in values)) or total != target:
        return {'key':'invalid','label':'Ошибка','sum':total,'reason':'Сумма должна быть 100%' if percentage else 'Сумма не совпадает с планом'}
    return {'key':'ready','label':'Задано','sum':total,'reason':''}


def power_status(group, required, *, models_required=True):
    powers = group['powers']
    status = values_status([power.get('share') for power in powers.values()],HUNDRED,required=required)
    if not required:
        return {**status,'key':'not_required','label':'Без плана','reason':'Нет объёма для распределения'}
    if status['key'] != 'ready':
        return status
    if not models_required:
        return status
    for power in powers.values():
        if not number(power.get('share'),optional=True):
            continue
        models = {str(model['id']):model for model in power['models']}
        if not power['active'] or any(str(power.get(kind)) not in models or models[str(power.get(kind))]['kind'] != kind for kind in ('indoor','outdoor')):
            return {**status,'key':'missing','label':'Не завершено','reason':'Выберите активные модели для всех ненулевых мощностей'}
        try:
            factor = number(power.get('factor'),optional=True)
            cost = number(power.get('indoor_cost_percent'),optional=True)
        except ValidationError:
            return {**status,'key':'invalid','label':'Ошибка','reason':'Некорректные параметры стоимости блоков'}
        if not factor or cost is None or cost > HUNDRED:
            return {**status,'key':'missing','label':'Не завершено','reason':'Заполните соотношение DDP и долю стоимости внутреннего блока'}
    return status


@precise
def overview(scenario, section, filters):
    months = planning_months(scenario)
    rows = []
    power_columns = list(dict.fromkeys(key for group in scenario.state['groups'].values() for key in group['powers']))
    for gid,group in scenario.state['groups'].items():
        quantities = {}
        for cid,line in group['channels'].items():
            try:
                quantities[cid] = quantity(scenario,line)
            except ValidationError:
                quantities[cid] = None
        sales_required = any(qty is None or qty > 0 for qty in quantities.values())
        stock_present = any(power.get('stock_indoor') is not None or power.get('stock_outdoor') is not None for power in group['powers'].values())
        powers = power_status(group,sales_required or stock_present,models_required=sales_required)
        if section == 'powers':
            rows.append({'gid':gid,'cid':next(iter(group['channels']),''),'name':group['ref']['name'],
                         'channels':list(scenario.state['channels'].values()), 'quantity':sum(quantities.values(),Decimal(0)) if all(qty is not None for qty in quantities.values()) else None,
                         'status':powers, 'powers':[{'name':key,'share':power.get('share')} for key,power in group['powers'].items()],
                         'source':group.get('distribution_sources',{}).get('powers'),
                         'channel_ids':set(group['channels'])})
            row = rows[-1]
            source = row['source']
            active = [power for power in group['powers'].values() if power['active']]
            row['auto_fact'] = bool(active) and sales_required and all(power.get('share') in (None,'') for power in active)
            row['mode'] = 'fact' if row['auto_fact'] else group.get('power_distribution_mode') or ('fact' if source and not source.get('adjusted') else 'manual')
            row['existing_fact'] = bool(source and not source.get('adjusted'))
            row['power_cells'] = []
            for key in power_columns:
                power = group['powers'].get(key)
                row['power_cells'].append({'key':key,'power':power,
                    'indoor_options':[model for model in power['models'] if model['kind'] == 'indoor'] if power else [],
                    'outdoor_options':[model for model in power['models'] if model['kind'] == 'outdoor'] if power else []})
            continue
        for cid,line in group['channels'].items():
            qty = quantities[cid]
            by_quantity = line.get('allocation') == 'quantity'
            values = [line.get('month_quantities',{}).get(month) if by_quantity else line['months'].get(month) for month in months]
            status = values_status(values,qty if by_quantity else HUNDRED,required=qty is None or qty > 0,percentage=not by_quantity)
            if qty is None:
                status = {**status,'key':'invalid','label':'Ошибка','reason':'Сначала исправьте объём плана или факт для годовой цели'}
            elif qty == 0:
                status = {**status,'key':'not_required','label':'Без плана','reason':'Нет объёма по каналу'}
            rows.append({'gid':gid,'cid':cid,'name':group['ref']['name'],'channel':scenario.state['channels'][cid]['name'],
                         'channel_ids':{cid}, 'quantity':qty,'status':status,'power_status':powers,
                         'by_quantity':by_quantity,'months':values,'month_cells':list(zip(months,values))})
            rows[-1]['source'] = group.get('distribution_sources',{}).get('months:'+cid)
            source = rows[-1]['source']
            mode = line.get('distribution_mode') or ('fact' if source and not source.get('adjusted') else 'manual')
            auto_fact = not by_quantity and qty is not None and qty > 0 and all(value in (None,'') for value in values)
            rows[-1]['mode'] = 'fact' if auto_fact else mode
            rows[-1]['auto_fact'] = auto_fact
    counts = {key:sum(row['status']['key'] == key for row in rows) for key in ('ready','missing','invalid','not_required')}
    filtered = []
    for row in rows:
        if filters.get('group') and row['gid'] != filters['group']:
            continue
        if filters.get('channel') and filters['channel'] not in row['channel_ids']:
            continue
        if filters.get('status') and row['status']['key'] != filters['status']:
            continue
        if filters.get('q') and filters['q'].casefold() not in row['name'].casefold():
            continue
        filtered.append(row)
    return {'rows':filtered,'counts':counts,'total':len(rows),'shown':len(filtered),'months':months,'power_columns':power_columns}
