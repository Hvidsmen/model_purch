"""Server-rendered brand pages; calculations and writes retain the full scenario."""
from copy import copy,deepcopy
import json
from urllib.parse import urlencode
from .split_plan_presentation import references


def brand_name(value):
    name=str(value or '').strip() or 'Без бренда'
    return 'Прочее' if len({part.strip().casefold() for part in name.split(',') if part.strip()})>1 else name


def buckets(scenario):
    refs=references(scenario);brands={}
    for gid in scenario.state['groups']:
        name=brand_name(refs.get(gid,{}).get('brand'))
        key=name.casefold();entry=brands.setdefault(key,{'key':key,'name':name,'gids':[]});entry['gids'].append(gid)
    return brands


def scope(scenario,params):
    brands=buckets(scenario)
    ordered=sorted(brands.values(),key=lambda row:row['name'].casefold())
    if len(ordered)<2:return scenario,None
    selected=params.get('brand_page','')
    if len({part.strip().casefold() for part in selected.split(',') if part.strip()})>1:selected='прочее'
    if params.get('focus')=='1':
        selected=next((row['key'] for row in ordered if params.get('group') in row['gids']),selected)
    if selected!='_all_' and selected not in brands:
        focus=params.get('group') or params.get('expand')
        selected=next((row['key'] for row in ordered if focus in row['gids']),ordered[0]['key'])
    query=params.copy()
    for field in ('page','focus','expand','group','export','filter_group'):query.pop(field,None)
    def url(key):
        query['brand_page']=key
        return '?'+query.urlencode() if hasattr(query,'urlencode') else '?'+urlencode(query)
    pages=[{'key':row['key'],'name':row['name'],'count':len(row['gids']),'url':url(row['key']),'selected':row['key']==selected} for row in ordered]
    chosen=brands.get(selected)
    if chosen:
        view=copy(scenario);view.state=deepcopy(scenario.state)
        view.state['groups']={gid:view.state['groups'][gid] for gid in chosen['gids']}
        index=next(i for i,row in enumerate(ordered) if row['key']==selected)
    else:view=scenario;index=None
    pager={'key':selected,'name':chosen['name'] if chosen else 'Все бренды','pages':pages,'total':len(pages),
           'number':index+1 if index is not None else None,'count':len(view.state['groups']),
           'previous':pages[index-1]['url'] if index is not None and index>0 else None,
           'next':pages[index+1]['url'] if index is not None and index+1<len(pages) else None,
           'all_url':url('_all_'),'group_ids':json.dumps(list(view.state['groups']))}
    return view,pager
