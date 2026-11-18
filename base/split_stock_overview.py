"""All scenario groups at stock grain: shared inventory, no channel duplication."""
from django.core.exceptions import ValidationError
from .split_engine import number,quantity,forecast_months,ZERO
from .split_opening import opening_balance


def complete_sum(values):
    values=list(values)
    return None if not values or any(value is None for value in values) else sum(values,ZERO)


def overview(scenario,evaluated=None):
    rows=[];ending={}
    for value in (evaluated or {}).get('stock',[]):ending.setdefault(value['group'],{})[value['power']]=value
    for gid,group in scenario.state['groups'].items():
        opening=opening_balance(scenario,group)
        openings={value['power']:value for value in opening['rows']}
        powers=[{**power,'key':key,'index':index,'opening':openings[key],'ending':ending.get(group['ref']['name'],{}).get(key)} for index,(key,power) in enumerate(group['powers'].items())]
        channels=[]
        for cid,line in group.get('channels',{}).items():
            try:planned=quantity(scenario,line)
            except ValidationError:planned=None
            channels.append({'id':cid,'name':scenario.state['channels'].get(cid,{}).get('name',cid),'planned':planned})
        plan_total=complete_sum(channel['planned'] for channel in channels)
        inventory_only=plan_total==ZERO and number(group.get('buy','0'),optional=True)==ZERO
        expected=list(group['powers']) if group.get('forecast_mode')=='powers' or inventory_only else [key for key,power in group['powers'].items() if power.get('share') and number(power['share'])>0]
        closed=ending.get(group['ref']['name'],{})
        closing=complete_sum(closed.get(key,{}).get('closing') for key in expected)
        rows.append({'id':gid,'group':group,'form_id':'split-stock-form-'+gid,'cid':next(iter(group.get('channels',{})),''),
                     'forecast_mode':group.get('forecast_mode','monthly'),'forecast_mode_choice':'powers' if group.get('forecast_mode')=='powers' else 'group','powers':powers,'opening':opening,'channels':channels,'planned':plan_total,'closing':closing,
                     'forecasts':[{'key':month,'value':group.get('forecast',{}).get(month)} for month in forecast_months(scenario)],
                     'incoming_indoor':complete_sum(number(power.get('incoming_indoor'),optional=True) for power in group['powers'].values()),
                     'incoming_outdoor':complete_sum(number(power.get('incoming_outdoor'),optional=True) for power in group['powers'].values())})
    totals={key:complete_sum(row['opening']['totals'][key] for row in rows) for key in ('stock_indoor','stock_outdoor','prior_indoor','prior_outdoor','forecast','opening_indoor','opening_outdoor','sets')}
    totals['forecast']=complete_sum(row['opening']['forecast_total'] for row in rows)
    totals.update(planned=complete_sum(row['planned'] for row in rows),incoming_indoor=complete_sum(row['incoming_indoor'] for row in rows),incoming_outdoor=complete_sum(row['incoming_outdoor'] for row in rows),closing=complete_sum(row['closing'] for row in rows))
    return {'groups':rows,'totals':totals}
