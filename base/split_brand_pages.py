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
    selected_keys=None
    if selected.startswith('['):
        try:
            values=json.loads(selected)
            if isinstance(values,list):selected_keys={key for key in values if isinstance(key,str) and key in brands}
        except (ValueError,TypeError):pass
    if selected_keys:
        selected=json.dumps(sorted(selected_keys),ensure_ascii=False) if len(selected_keys)>1 else next(iter(selected_keys))
    elif len({part.strip().casefold() for part in selected.split(',') if part.strip()})>1:selected='прочее'
    if params.get('focus')=='1':
        selected=next((row['key'] for row in ordered if params.get('group') in row['gids']),selected)
        selected_keys=None
    if selected!='_all_' and selected not in brands and not selected_keys:
        focus=params.get('group') or params.get('expand')
        selected=next((row['key'] for row in ordered if focus in row['gids']),ordered[0]['key'])
    if selected=='_all_':selected_keys=set(brands)
    elif selected in brands:selected_keys={selected}
    query=params.copy()
    for field in ('page','focus','expand','group','export','filter_group'):query.pop(field,None)
    def url(key):
        query['brand_page']=key
        return '?'+query.urlencode() if hasattr(query,'urlencode') else '?'+urlencode(query)
    pages=[{'key':row['key'],'name':row['name'],'count':len(row['gids']),'url':url(row['key']),'selected':row['key'] in selected_keys} for row in ordered]
    chosen=brands.get(selected)
    if selected!='_all_':
        view=copy(scenario);view.state=deepcopy(scenario.state)
        view.state['groups']={gid:view.state['groups'][gid] for row in ordered if row['key'] in selected_keys for gid in row['gids']}
        index=next((i for i,row in enumerate(ordered) if row['key']==selected),None)
    else:view=scenario;index=None
    pager={'key':selected,'name':chosen['name'] if chosen else ('Выбранные бренды' if selected!='_all_' else 'Все бренды'),'caption':chosen['name'] if chosen else ('Выбрано: '+str(len(selected_keys)) if selected!='_all_' else 'Все бренды'),'pages':pages,'total':len(pages),
           'number':index+1 if index is not None else None,'count':len(view.state['groups']),
           'previous':pages[index-1]['url'] if index is not None and index>0 else None,
           'next':pages[index+1]['url'] if index is not None and index+1<len(pages) else None,
           'all_url':url('_all_'),'group_ids':json.dumps(list(view.state['groups']))}
    return view,pager
