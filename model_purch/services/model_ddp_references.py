"""Filters use effective per-model classification, including snapshot defaults."""
from types import SimpleNamespace
from django.core.cache import cache
from django.db.models import Exists,OuterRef,Q
from .model_ddp import parameters

FILTERS=(('planning_group','Группа планирования'),('planning_sales','Группа планов продаж'),
         ('group_goods','Группа товаров'),('brand','Марка'),('purch','Закупка'),
         ('kind_purch','Вид закупки'),('supply_type','Тип поставки'))
def catalog(batch):
    key=f'model-ddp-reference-v2:{batch.pk}:{batch.created_at.isoformat()}:{batch.revision}'
    found=cache.get(key)
    if found is not None:return found
    entries=[];options={key:set() for key,label in FILTERS}
    for data in batch.rows.values('pk','planning_group','brand','overrides','model_name','price_type','source_data').iterator(chunk_size=1000):
        row=SimpleNamespace(**data)
        p=parameters(batch,row)
        effective={key:str(data['planning_group'] if key=='planning_group' else p.get(key) or '') for key,label in FILTERS}
        for name,value in effective.items():
            if value:options[name].add(value)
        search=' '.join([data['model_name'],data['price_type'],*effective.values()]).casefold()
        entries.append((data['pk'],effective,search))
    result=(entries,{key:sorted(values,key=str.casefold) for key,values in options.items()})
    cache.set(key,result,300)
    return result

def filter_data(batch,request):
    entries,options=catalog(batch)
    selected={key:request.GET.get(key,'') for key,label in FILTERS}
    q=request.GET.get('q','').strip().casefold()
    supplier_missing=request.GET.get('price_check')=='supplier'
    active=q or any(selected.values()) or supplier_missing
    ids=None
    if active:
        ids={pk for pk,values,text in entries if (not q or q in text) and (not supplier_missing or values['supply_type'] not in ('direct','p2','russian')) and
             all(not value or (not values[key] if value=='__empty__' else values[key]==value) for key,value in selected.items())}
    controls=[{'key':key,'label':label,'value':selected[key],'options':options[key]} for key,label in FILTERS]
    return ids,controls,options


def visible_prices(query):
    """A zero is only useful when the model has no nonzero source price."""
    nonzero=query.model.objects.filter(calculation_id=OuterRef('calculation_id'),model_ref=OuterRef('model_ref')).exclude(price__isnull=True).exclude(price=0)
    return query.alias(has_nonzero_price=Exists(nonzero)).exclude(Q(price=0)&Q(has_nonzero_price=True))


def apply_query_filters(query,filters):
    query=visible_prices(query)
    if filters.get('currency'):query=query.filter(currency=filters['currency'])
    state=filters.get('status')
    if state in ('ready','partial','error'):query=query.filter(result__status=state)
    elif state=='pending':query=query.filter(result={})
    issue=filters.get('price_check')
    if issue=='zero':query=query.filter(price=0)
    elif issue=='missing':query=query.filter(price__isnull=True)
    elif issue=='currency':query=query.exclude(currency__in=('USD','EUR','CNY','RUB'))
    return query
