"""Cost parameters from the DDP stock register, independent of power shares."""
from copy import deepcopy
from decimal import Decimal
import json
from django.core import signing
from django.core.exceptions import ValidationError
from .split_ddp import stock
from .split_engine import number,stored,precise,STEP
from .split_history import normalized

SALT='split-power-stock-parameters-v1'


@precise
def parameters(group,current,costs,only_empty):
    active={key:power for key,power in group['powers'].items() if power['active']}
    if not isinstance(current,dict) or set(current)!=set(active):raise ValidationError('Передайте параметры всех активных мощностей.')
    output={};kits={};units={};issues=[]
    source_key=group['ref']['source_key']
    for key,power in active.items():
        params=current[key]
        if not isinstance(params,dict) or set(params)!={'factor','cost','indoor','outdoor'}:raise ValidationError('Некорректные параметры мощности.')
        params=deepcopy(params)
        for field in ('factor','cost'):
            value=number(params[field],optional=True)
            if value is not None and (field=='factor' and value<=0 or field=='cost' and value>100):raise ValidationError('Некорректная стоимость мощности.')
            params[field]=stored(value)
        prices={}
        for kind in ('indoor','outdoor'):
            params[kind]=params[kind] or None
            models=[model for model in power['models'] if model['kind']==kind]
            if params[kind] and not any(model['id']==params[kind] for model in models):raise ValidationError('Выбранная модель не входит в снимок мощности.')
            candidates=[]
            for model in models:
                entry=costs.get((source_key,normalized(model['name'])))
                if entry and not entry['unknown'] and entry['qty']>0 and entry['cost']>0:candidates.append((model,entry))
            if not params[kind]:
                maximum=max((entry['qty'] for model,entry in candidates),default=Decimal(0))
                leaders=[model for model,entry in candidates if entry['qty']==maximum]
                if len(leaders)==1:params[kind]=leaders[0]['id']
            selected=next(((model,entry) for model,entry in candidates if model['id']==params[kind]),None)
            if selected:prices[kind]=selected[1]['cost']/selected[1]['qty']
            else:issues.append(key+': нет положительного DDP остатков выбранного '+('внутреннего' if kind=='indoor' else 'наружного')+' блока.')
        if len(prices)==2:
            kits[key]=prices['indoor']+prices['outdoor'];units[key]=prices
        output[key]=params
    base=min(kits.values(),default=None)
    for key,kit in kits.items():
        computed={'factor':(kit/base).quantize(STEP),'cost':(units[key]['indoor']*100/kit).quantize(STEP)}
        for field,value in computed.items():
            if not only_empty or output[key][field] is None:
                number(value)
                if field=='factor' and value<=0:raise ValidationError('Соотношение DDP вне допустимой точности.')
                output[key][field]=stored(value)
    return output,list(dict.fromkeys(issues))


def preview(scenario,raw,only_empty='1'):
    if only_empty not in ('0','1'):raise ValidationError('Выберите режим заполнения стоимости.')
    if len(raw)>2_000_000:raise ValidationError('Слишком большой список групп.')
    try:rows=json.loads(raw)
    except (ValueError,TypeError):raise ValidationError('Некорректный список групп.')
    if not isinstance(rows,list) or not rows or len(rows)>len(scenario.state['groups']):raise ValidationError('Выберите группы сценария.')
    seen=set()
    for row in rows:
        if not isinstance(row,dict) or not isinstance(row.get('gid'),str) or row['gid'] not in scenario.state['groups'] or row['gid'] in seen:raise ValidationError('Выберите доступные группы без повторов.')
        seen.add(row['gid'])
    costs,origin=stock(scenario)
    result=[]
    for row in rows:
        group=scenario.state['groups'][row['gid']]
        params,issues=parameters(group,row.get('parameters'),costs,only_empty=='1')
        source={**origin,'kind':'stock_ddp','label':'DDP из стока на '+str(scenario.stock_date),'only_empty':only_empty=='1','issues':issues,
                'rule':'DDP блока = стоимость остатков / количество. DDP комплекта = внутренний + наружный. Соотношение = DDP комплекта / минимальный DDP комплекта группы.'}
        token=signing.dumps({'identity':[scenario.pk,scenario.revision,row['gid']],'parameters':params,'source':source},salt=SALT,compress=True)
        result.append({'gid':row['gid'],'parameters':params,'token':token,'source':source})
    return result


def apply_source(scenario,group,gid,token):
    try:
        signed=signing.loads(token,salt=SALT,max_age=86400)
        if signed['identity']!=[scenario.pk,scenario.revision,gid]:raise signing.BadSignature
        actual={key:{'factor':power.get('factor'),'cost':power.get('indoor_cost_percent'),'indoor':power.get('indoor'),'outdoor':power.get('outdoor')}
                for key,power in group['powers'].items() if power['active']}
        proposed=deepcopy(signed['parameters'])
        for collection in (actual,proposed):
            for params in collection.values():
                for field in ('factor','cost'):params[field]=number(params[field],optional=True)
                for kind in ('indoor','outdoor'):params[kind]=params[kind] or None
        group['power_cost_source']={**signed['source'],'adjusted':actual!=proposed}
    except (signing.BadSignature,KeyError,TypeError,ValueError):raise ValidationError('Предложение DDP из стока устарело. Получите его заново.')
