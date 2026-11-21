"""Explicit, scoped scenario transfers. Inventory is never shifted to another date."""
from copy import deepcopy
from datetime import date
import json
from django.core.exceptions import ValidationError
from django.utils import timezone
from .sales_planning_access import check_scenario,groups_for
from .split_workflow import group_snapshot,empty_line
from .split_engine import planning_months,forecast_months,quantity,stored,number,ZERO,allocate,HUNDRED

STAGES={'composition','months','powers','opening','targets','plan','all'}


def candidate(target,source,actor,stage,fill='append'):
    check_scenario(actor,source);check_scenario(actor,target)
    if target.status!='draft':raise ValidationError('Копирование доступно только в черновик.')
    if source.pk==target.pk or source.start>target.start:raise ValidationError('Выберите предыдущий сценарий с началом не позже текущего.')
    if stage not in STAGES or fill not in ('append','replace'):raise ValidationError('Выберите шаг и режим копирования.')
    state=deepcopy(target.state);notes=[];count=0
    same_dates=(target.stock_date,target.start,target.end)==(source.stock_date,source.start,source.end)
    origin={'scenario':source.pk,'title':source.title,'version':source.version,'revision':source.revision,'stock_date':str(source.stock_date),'at':timezone.now().isoformat()}
    stages=['composition','months','powers','targets','plan','opening'] if stage=='all' else [stage]
    if 'composition' in stages:
        # Add source members without excluding current groups or rewriting the directory.
        state['channels'].update(deepcopy(source.state['channels']))
        directory={g.source_key:g for g in groups_for(actor)}
        known={g['ref']['source_key'] for g in state['groups'].values()}
        for source_group in source.state['groups'].values():
            key=source_group['ref']['source_key']
            if key in known:continue
            if key not in directory:notes.append(source_group['ref']['name']+': группа отсутствует в доступном справочнике.');continue
            obj=directory[key];g=group_snapshot(obj);g['forecast']={m:None for m in forecast_months(target)};g['channels']={cid:empty_line(target) for cid in state['channels']}
            state['groups'][str(obj.pk)]=g;known.add(key);count+=1
        for g in state['groups'].values():
            for cid in state['channels']:g['channels'].setdefault(cid,empty_line(target))
    sources={g['ref']['source_key']:g for g in source.state['groups'].values()}
    def shape(g):return {(key,tuple(sorted((m['kind'],m['key']) for m in power['models']))) for key,power in g['powers'].items()}
    same_dates=same_dates and all(shape(g)==shape(sources[g['ref']['source_key']]) for g in state['groups'].values() if g['ref']['source_key'] in sources)
    source_channels={str(ch['name']).strip().casefold():cid for cid,ch in source.state['channels'].items()}
    def put(obj,key,value,zero=False):
        nonlocal count
        if value is None:return
        if fill=='replace' or obj.get(key) in ((None,'','0',0) if zero else (None,'')):
            obj[key]=deepcopy(value);count+=1
    def months(values):
        result={}
        for m in planning_months(target):
            key=f'{int(m[:4])-target.start.year+source.start.year}-{m[-2:]}'
            alternatives=[v for old,v in values.items() if old[-2:]==m[-2:]]
            result[m]=values[key] if key in values else alternatives[0] if len(alternatives)==1 else None
        weights=[number(v,optional=True) for v in result.values()]
        if weights and all(v is not None for v in weights) and sum(weights,ZERO):return dict(zip(result,map(stored,allocate(HUNDRED,weights))))
        return result
    for gid,g in state['groups'].items():
        src=sources.get(g['ref']['source_key'])
        if not src:notes.append(g['ref']['name']+': нет соответствующей группы в источнике.');continue
        if 'composition' in stages:
            permitted={obj.pk for obj in directory.values()}
            for field in ('related','replacements'):
                if fill=='replace' or not g['ref'].get(field):
                    g['ref'][field]=deepcopy([link for link in src['ref'].get(field,[]) if link.get('pk') in permitted])
        if 'powers' in stages:
            for key,p in g['powers'].items():
                old=src['powers'].get(key)
                if not old:notes.append(g['ref']['name']+' · '+key+': нет мощности в источнике.');continue
                for field in ('share','factor','indoor_cost_percent'):put(p,field,old.get(field))
                for field,kind in [('indoor','indoor'),('outdoor','outdoor')]:
                    selected=next((m for m in old['models'] if str(m['id'])==str(old.get(field)) and m['kind']==kind),None)
                    options=[m for m in p['models'] if selected and m['kind']==kind and m['key']==selected['key']]
                    if not options:options=[m for m in p['models'] if selected and m['kind']==kind and m['name'].strip().casefold()==selected['name'].strip().casefold()]
                    matched=options[0] if len(options)==1 else None
                    if matched:put(p,field,matched['id'])
                    elif selected:notes.append(g['ref']['name']+' · '+key+': выбранная модель отсутствует в новом составе.')
        if 'targets' in stages:
            before=g.get('target_stock_percent');put(g,'target_stock_percent',src.get('target_stock_percent'))
            if before!=g.get('target_stock_percent'):g['target_stock_source']={'kind':'scenario','label':'Из сценария '+source.title,**origin}
        for cid,line in g['channels'].items():
            old=src['channels'].get(source_channels.get(state['channels'][cid]['name'].strip().casefold()))
            if not old:continue
            if 'months' in stages:
                mapped=months(old.get('month_quantities',{}) if old.get('allocation')=='quantity' else old.get('months',{}))
                for m,value in mapped.items():put(line.setdefault('months',{}),m,value)
                if fill=='replace':
                    line.pop('month_quantities',None);line['allocation']='shares';line['distribution_mode']='manual'
            if 'plan' in stages:
                previous_ddp=g.get('ddp');put(g,'ddp',src.get('ddp'),True)
                if previous_ddp!=g.get('ddp'):g['ddp_source']={'source':'Скопировано из сценария '+source.title,'copy_scenario':source.pk,'copy_title':source.title}
                if not line.get('lock_markup'):put(line,'markup',old.get('markup'),True)
                if not line.get('lock_qty') and (fill=='replace' or line.get('input') in (None,'','0',0)):
                    try:
                        from .split_group_plan import set_period_quantity
                        updated=deepcopy(line);set_period_quantity(target,state,updated,line,quantity(source,old));line.update(updated);state['sales_reviewed_groups']=sorted(set(state.get('sales_reviewed_groups',[]))|{gid});count+=1
                    except ValidationError as exc:notes.append(g['ref']['name']+': '+' '.join(exc.messages))
        if 'opening' in stages and same_dates:
            from .split_forecast_history import has_forecast
            copy_forecast=fill=='replace' or not has_forecast(target,g)
            for key,p in g['powers'].items():
                old=src['powers'].get(key,{})
                for field in ('stock_indoor','stock_outdoor','prior_indoor','prior_outdoor','incoming_indoor','incoming_outdoor','incoming_sql_indoor','incoming_sql_outdoor'):
                    put(p,field,old.get(field))
                if copy_forecast:put(p,'forecast_before',old.get('forecast_before'))
            if fill=='replace' or not g.get('inventory_source'):
                if src.get('inventory_source'):g['inventory_source']=deepcopy(src['inventory_source'])
            if copy_forecast:
                for field in ('forecast_mode','forecast_before_total','forecast','forecast_imports','forecast_history_source','forecast_workspace_source'):
                    g.pop(field,None)
                    if field in src:g[field]=deepcopy(src[field])
        g.setdefault('scenario_copy_sources',{})[stage]=origin
    draft=deepcopy(target);draft.state=state
    if 'opening' in stages and not same_dates:
        recalc={gid:g for gid,g in state['groups'].items() if fill=='replace' or not g.get('inventory_source') or g['inventory_source'].get('stock_date')!=str(target.stock_date)}
        # Reset the old cut-off before contacting a source; a failed refresh stays unknown.
        for g in recalc.values():
            for p in g['powers'].values():
                for field in list(p):
                    if field.startswith(('stock_','prior_','incoming_')) or field=='forecast_before':p[field]=None
            for field in ('inventory_source','forecast_history_source','forecast_workspace_source','forecast_before_total','forecast_imports'):g.pop(field,None)
            g['forecast_mode']='powers';g['forecast']={m:None for m in forecast_months(target)}
            g['buy']='0';g.pop('buy_source',None)
        try:
            from .split_inventory import source_rows_many,apply_inventory
            sources_inventory=source_rows_many(draft,recalc) if recalc else {}
            for gid,g in recalc.items():
                try:
                    updated=deepcopy(g);apply_inventory(draft,updated,sources_inventory[gid]);state['groups'][gid]=updated
                except ValidationError as exc:notes.append(g['ref']['name']+': остатки не загружены — '+' '.join(exc.messages))
            draft.state=state
        except ValidationError as exc:notes.append('Остатки на новую дату не загружены: '+' '.join(exc.messages))
        else:
            from .split_forecast_history import preview,apply
            batches={}
            for gid,g in list(state['groups'].items()):
                if gid not in recalc or not g.get('inventory_source'):continue
                if not forecast_months(target):
                    for p in g['powers'].values():p['forecast_before']='0'
                    continue
                src=sources.get(g['ref']['source_key'],{});old=src.get('forecast_history_source') or {}
                params={'targets':json.dumps([gid]),'method':'history','year':str(target.stock_date.year+int(old.get('year',source.stock_date.year))-source.stock_date.year),'adjustment':str(old.get('adjustment_percent',0))}
                if old.get('method')=='plan_execution':
                    if source.stock_date.year!=target.stock_date.year:
                        notes.append(g['ref']['name']+': выберите версию ERP для нового года на шаге начального запаса.');continue
                    params.update(method='plan_execution',plan_version=old.get('plan_version',''),elapsed_from=old.get('from',str(date(target.stock_date.year,1,1))))
                params.pop('targets',None)
                identity=tuple(sorted(params.items()))
                batches.setdefault(identity,[]).append(gid)
            for identity,ids in batches.items():
                try:
                    result=preview(draft,{**dict(identity),'targets':json.dumps(ids)})
                    tokens=[row['token'] for row in result['rows'] if row.get('token')]
                    notes.extend(row['name']+': прогноз не рассчитан — '+row.get('error','Нет результата.') for row in result['rows'] if not row.get('token'))
                    if tokens:
                        state,applied=apply(draft,{'tokens':json.dumps(tokens)});draft.state=state;count+=applied
                except ValidationError as exc:notes.append('Прогноз не рассчитан — '+' '.join(exc.messages))
    if 'plan' in stages or ('opening' in stages and not same_dates):
        from .split_stock_workspace import presentation,supply_proposal
        draft.state=state
        for row in presentation(draft)['rows']:
            try:
                g=state['groups'][row['id']]
                if fill=='append' and g.get('buy') not in (None,'','0',0):continue
                g['buy']=stored(supply_proposal(draft,g,row));g['buy_source']={'kind':'calculated','label':'Пересчитано при копировании',**origin}
            except ValidationError as exc:notes.append(row['group']['ref']['name']+': закупки не рассчитаны — '+' '.join(exc.messages))
    state.setdefault('scenario_copy_log',[]).append({**origin,'stage':stage,'fill':fill,'target_stock_date':str(target.stock_date),'notes':notes})
    check_scenario(actor,target,state)
    return state,{'count':count,'notes':notes}
