"""Filters use effective per-model classification, including snapshot defaults."""
from types import SimpleNamespace
from django.core.cache import cache
from .model_ddp import parameters

FILTERS=(('planning_group','Группа планирования'),('planning_sales','Группа планов продаж'),
         ('group_goods','Группа товаров'),('brand','Марка'),('purch','Закупка'),
         ('kind_purch','Вид закупки'),('supply_type','Тип поставки'))
def catalog(batch):
    key=f'model-ddp-reference-v1:{batch.pk}:{batch.created_at.isoformat()}:{batch.revision}'
    found=cache.get(key)
    if found is not None:return found
    entries=[];options={key:set() for key,label in FILTERS}
    for data in batch.rows.values('pk','planning_group','brand','overrides','model_name','price_type').iterator(chunk_size=1000):
        row=SimpleNamespace(source_data={},**data)
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
    active=q or any(selected.values())
    ids=None
    if active:
        ids={pk for pk,values,text in entries if (not q or q in text) and
             all(not value or (not values[key] if value=='__empty__' else values[key]==value) for key,value in selected.items())}
    controls=[{'key':key,'label':label,'value':selected[key],'options':options[key]} for key,label in FILTERS]
    return ids,controls,options
