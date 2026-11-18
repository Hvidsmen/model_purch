"""All scenario groups at stock grain: shared inventory, no channel duplication."""
from django.core.exceptions import ValidationError
from .split_engine import number,quantity,forecast_months,ZERO
from .split_opening import opening_balance
from .split_supplies import signed_quantity, sql_receipts, corrections, spread


def complete_sum(values):
    values=list(values)
    return None if not values or any(value is None for value in values) else sum(values,ZERO)


def overview(scenario,evaluated=None):
    rows=[];ending={};channel_powers={}
    for value in (evaluated or {}).get('shipments',[]):
        if value['kind']=='indoor':
            key=(value['group_key'],value['power'],value['channel_id'])
            channel_powers[key]=channel_powers.get(key,ZERO)+value['quantity']
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
        for power in powers:
            for field in ('stock','prior','incoming'):
                values=[power['opening'].get(field+'_'+kind) if field!='incoming' else sql_receipts(power,kind) for kind in ('indoor','outdoor')]
                power[field+'_sets']=None if any(value is None for value in values) else min(values)
            for kind in ('indoor','outdoor'):
                power['incoming_'+kind]=sql_receipts(power,kind)
            power['channels']=[{**channel,'planned':ZERO if channel['planned']==ZERO else
                channel_powers.get((group['ref']['source_key'],power['key'],channel['id']))} for channel in channels]
        inventory_only=plan_total==ZERO and number(group.get('buy','0'),optional=True)==ZERO
        expected=list(group['powers']) if group.get('forecast_mode')=='powers' or inventory_only else [key for key,power in group['powers'].items() if power.get('share') and number(power['share'])>0]
        closed=ending.get(group['ref']['name'],{})
        closing=complete_sum(closed.get(key,{}).get('closing') for key in expected)
        adjustment = signed_quantity(group.get('supply_adjustment','0'))
        new_supply = number(group.get('buy','0'))
        try: adjusted_powers = corrections(group)
        except ValidationError: adjusted_powers = {}
        try: new_powers = spread(group,new_supply)
        except ValidationError: new_powers = {}
        for power in powers:
            power['supply_adjustment'] = adjusted_powers.get(power['key'])
            power['new_supply'] = new_powers.get(power['key'])
        rows.append({'id':gid,'group':group,'form_id':'split-stock-form-'+gid,'cid':next(iter(group.get('channels',{})),''),
                     'supply_adjustment':adjustment,'new_supply':new_supply,
                     **{field+'_sets':complete_sum(power[field+'_sets'] for power in powers) for field in ('stock','prior','incoming')},
                     'forecast_mode':group.get('forecast_mode','monthly'),'forecast_mode_choice':'powers' if group.get('forecast_mode')=='powers' else 'group','powers':powers,'opening':opening,'channels':channels,'planned':plan_total,'closing':closing,
                     'forecasts':[{'key':month,'value':group.get('forecast',{}).get(month)} for month in forecast_months(scenario)],
                     'incoming_indoor':complete_sum(sql_receipts(power,'indoor') for power in group['powers'].values()),
                     'incoming_outdoor':complete_sum(sql_receipts(power,'outdoor') for power in group['powers'].values())})
    totals={key:complete_sum(row['opening']['totals'][key] for row in rows) for key in ('stock_indoor','stock_outdoor','prior_indoor','prior_outdoor','forecast','opening_indoor','opening_outdoor','sets')}
    totals['forecast']=complete_sum(row['opening']['forecast_total'] for row in rows)
    totals.update({field:complete_sum(row[field] for row in rows) for field in ('supply_adjustment','new_supply')})
    totals.update({field+'_sets':complete_sum(row[field+'_sets'] for row in rows) for field in ('stock','prior','incoming')})
    totals.update(planned=complete_sum(row['planned'] for row in rows),incoming_indoor=complete_sum(row['incoming_indoor'] for row in rows),incoming_outdoor=complete_sum(row['incoming_outdoor'] for row in rows),closing=complete_sum(row['closing'] for row in rows))
    channels=[{'id':cid,'name':channel['name'],'planned':complete_sum(
        next((item['planned'] for item in row['channels'] if item['id']==cid),None) for row in rows)}
        for cid,channel in scenario.state['channels'].items()]
    return {'groups':rows,'totals':totals,'channels':channels,'expanded_colspan':17+len(channels)}
