"""Read-only brand report and revision-bound, explicitly confirmed adjustments."""
from copy import deepcopy
from decimal import Decimal
from urllib.parse import urlencode
from django.core import signing
from django.core.exceptions import ValidationError
from .split_brand_pages import buckets
from .split_engine import evaluate,number,quantity,allocate,stored,ZERO,precise
from .split_matrix_totals import aggregate
from .split_group_plan import set_period_quantity
from .split_stock_overview import overview


def report(scenario):
    data=evaluate(scenario);inventory=overview(scenario,data)
    stock={row['id']:row for row in inventory['groups']}
    rows=[]
    for bucket in sorted(buckets(scenario).values(),key=lambda row:row['name'].casefold()):
        ids=set(bucket['gids']);lines=[{**line,'qty':line['quantity']} for line in data['lines'] if line['gid'] in ids]
        endings=[stock[gid]['closing'] for gid in ids]
        rows.append({**bucket,'total':aggregate(lines),'closing':sum(endings,ZERO) if all(v is not None for v in endings) else None,
            'url':'?'+urlencode({'tab':'plan','brand_page':bucket['key']}),
            'channels':[{'id':cid,'name':ch['name'],**aggregate(line for line in lines if line['cid']==cid)} for cid,ch in scenario.state['channels'].items()]})
    return {'rows':rows,'total':aggregate({**line,'qty':line['quantity']} for line in data['lines']),'closing':inventory['totals']['closing']}


@precise
def candidate(scenario,params):
    bucket=buckets(scenario).get(params.get('brand'))
    cid=params.get('channel')
    if not bucket or cid not in scenario.state['channels'] and cid!='_all_':raise ValidationError('Выберите бренд и канал сценария.')
    qty=params.get('quantity','');markup=params.get('markup','')
    if not qty and not markup:raise ValidationError('Укажите новый объём или наценку.')
    method=params.get('method','saved')
    if method not in ('saved','equal'):raise ValidationError('Выберите способ распределения.')
    state=deepcopy(scenario.state)
    entries=[(gid,state['groups'][gid],key,state['groups'][gid]['channels'][key],scenario.state['groups'][gid]['channels'][key]) for gid in bucket['gids'] for key in scenario.state['channels'] if cid=='_all_' or key==cid]
    if qty:
        target=number(qty);fixed=sum((quantity(scenario,line) for _,_,_,line,_ in entries if line['lock_qty']),ZERO)
        free=[(line,previous) for _,_,_,line,previous in entries if not line['lock_qty']]
        weights=[Decimal(1) if method=='equal' else quantity(scenario,previous) for _,previous in free]
        if target<fixed or not free and target!=fixed:raise ValidationError('Новый объём несовместим с закреплениями.')
        if target>fixed and not sum(weights,ZERO):raise ValidationError('Нет исходных пропорций. Выберите равномерное распределение.')
        values=allocate(target-fixed,weights) if sum(weights,ZERO) else [ZERO]*len(free)
        for (line,previous),value in zip(free,values):set_period_quantity(scenario,state,line,previous,value)
    if markup:
        target=number(markup);cost=ZERO;fixed_revenue=ZERO;free=[]
        for _,g,_,line,_ in entries:
            ddp=number(g.get('ddp'),optional=True)
            if ddp is None or ddp<=0:raise ValidationError('Заполните положительный DDP всех групп бренда.')
            subtotal=quantity(scenario,line)*ddp;cost+=subtotal
            if line['lock_markup']:
                old_markup=number(line.get('markup'),optional=True)
                if old_markup is None:raise ValidationError('У закреплённой строки не заполнена наценка.')
                fixed_revenue+=subtotal*old_markup
            else:free.append((line,subtotal))
        desired=cost*target-fixed_revenue;free_cost=sum((c for _,c in free),ZERO)
        if not cost:raise ValidationError('Для изменения наценки задайте положительный объём.')
        if desired<0 or not free_cost and desired!=0:raise ValidationError('Новая наценка несовместима с закреплениями.')
        # Scale existing relative markups where possible, otherwise use a common markup.
        base=sum((c*(number(line.get('markup'),optional=True) or ZERO) for line,c in free),ZERO)
        for line,c in free:
            value=(number(line.get('markup'),optional=True) or ZERO)*desired/base if base else desired/free_cost if free_cost else ZERO
            line['markup']=stored(value.quantize(Decimal('.000001')));number(line['markup'])
        achieved=fixed_revenue+sum((c*number(line['markup']) for line,c in free),ZERO)
        if abs(achieved-cost*target)>=Decimal('.005'):raise ValidationError('При точности наценки 6 знаков итог нельзя получить до цента. Измените цель или закрепления.')
    if qty:state['sales_reviewed_groups']=sorted(set(state.get('sales_reviewed_groups',[]))|set(bucket['gids']))
    draft=deepcopy(scenario);draft.state=state
    before=evaluate(scenario);after=evaluate(draft)
    def totals(data,item):
        total=aggregate({**line,'qty':line['quantity']} for line in data['lines'] if line['gid'] in bucket['gids'] and (cid=='_all_' or line['cid']==cid))
        endings=[row['closing'] for row in overview(item,data)['groups'] if row['id'] in bucket['gids']]
        total['closing']=sum(endings,ZERO) if all(value is not None for value in endings) else None
        return total
    changed=[];changed_groups=set()
    for gid,group,key,line,previous in entries:
        if line!=previous:
            changed_groups.add(gid)
            changed.append({'name':group['ref']['name']+' · '+scenario.state['channels'][key]['name'],'before_qty':quantity(scenario,previous),'after_qty':quantity(draft,line),'before_markup':previous.get('markup'),'after_markup':line.get('markup')})
    return state,{'brand':bucket['name'],'channel':'Все каналы' if cid=='_all_' else scenario.state['channels'][cid]['name'],'before':totals(before,scenario),'after':totals(after,draft),
        'changed':changed,'count':len(changed_groups),'locked_qty':sum(line['lock_qty'] for _,_,_,line,_ in entries),'locked_markup':sum(line['lock_markup'] for _,_,_,line,_ in entries),'method':method}



def preview(scenario,post):
    params={key:str(post.get(key,'')).strip() for key in ('brand','channel','quantity','markup','method')}
    _,result=candidate(scenario,params)
    result['token']=signing.dumps({'scenario':scenario.pk,'revision':scenario.revision,'params':params},salt='split-brand-adjustment',compress=True)
    return result


def apply(scenario,post):
    if post.get('confirm')!='on':raise ValidationError('Подтвердите показанную корректировку.')
    try:
        payload=signing.loads(post.get('token',''),salt='split-brand-adjustment',max_age=3600)
        if payload['scenario']!=scenario.pk or payload['revision']!=scenario.revision:raise signing.BadSignature
    except (signing.BadSignature,KeyError,TypeError):raise ValidationError('Предпросмотр устарел. Рассчитайте корректировку заново.')
    return candidate(scenario,payload['params'])[0]
