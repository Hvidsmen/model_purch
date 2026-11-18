"""All scenario groups at stock grain: shared inventory, no channel duplication."""
from django.core.exceptions import ValidationError
from .split_engine import number,quantity,forecast_months,ZERO
from .split_opening import opening_balance


def complete_sum(values):
    values=list(values)
    return None if not values or any(value is None for value in values) else sum(values,ZERO)


def overview(scenario):
    rows=[]
    for gid,group in scenario.state['groups'].items():
        opening=opening_balance(scenario,group)
        powers=[{**power,'key':key,'index':index} for index,(key,power) in enumerate(group['powers'].items())]
        channels=[]
        for cid,line in group.get('channels',{}).items():
            try:planned=quantity(scenario,line)
            except ValidationError:planned=None
            channels.append({'id':cid,'name':scenario.state['channels'].get(cid,{}).get('name',cid),'planned':planned})
        rows.append({'id':gid,'group':group,'form_id':'split-stock-form-'+gid,'cid':next(iter(group.get('channels',{})),''),
                     'powers':powers,'opening':opening,'channels':channels,
                     'forecasts':[{'key':month,'value':group.get('forecast',{}).get(month)} for month in forecast_months(scenario)],
                     'incoming_indoor':complete_sum(number(power.get('incoming_indoor'),optional=True) for power in group['powers'].values()),
                     'incoming_outdoor':complete_sum(number(power.get('incoming_outdoor'),optional=True) for power in group['powers'].values())})
    totals={key:complete_sum(row['opening']['totals'][key] for row in rows) for key in ('stock_indoor','stock_outdoor','prior_indoor','prior_outdoor','forecast','opening_indoor','opening_outdoor','sets')}
    return {'groups':rows,'totals':totals}
