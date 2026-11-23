"""Independent per-model DDP snapshots, reusing the purchase pricing formula."""
from copy import deepcopy
from hashlib import sha256
import json
from decimal import Decimal,InvalidOperation
from types import SimpleNamespace
from django.core.exceptions import ValidationError
from django.core import signing
from django.db import transaction
from django.utils import timezone
from ..models import ModelDDPCalculation,ModelDDPRow,ScenarioModel,Freight,GoodsGroup
from ..goods_identity import planning_group_key
from .pricing import apply_price,CALCULATED_FIELDS

CURRENCIES=('USD','EUR','CNY','RUB')
MODEL_FIELDS=('container_volume','duty_percent','supply_type','planning_sales','group_goods','brand','purch','kind_purch')
FIELDS=('volume','weight_kg',*MODEL_FIELDS,'price_per_container','customs_rate','warehouse_delivery_cost','foreign_delivery_cost','nr_customs_vat_rate')
FREIGHT_FIELDS=('price_per_container','customs_rate','warehouse_delivery_cost','foreign_delivery_cost','nr_customs_vat_rate')
SALT='purchase-model-ddp-parameters-v1'


def decimal(value,label,optional=False,positive=False):
    if optional and value in (None,''):return None
    try:
        value=Decimal(str(value).replace(',','.'))
        if not value.is_finite() or value<0 or value>Decimal('1000000000000') or positive and value==0:raise ValueError
        return value
    except (ValueError,InvalidOperation,TypeError):raise ValidationError(label+': некорректное значение.')


def goods_group_parameters(batch):
    if not hasattr(batch,'_ddp_goods_groups'):
        batch._ddp_goods_groups={group.name_key:{
            'container_volume':str(group.container_volume),
            'duty_percent':str(Decimal(str(group.duty_rate))*100)}
            for group in GoodsGroup.objects.all()}
    return batch._ddp_goods_groups


def directory_signature(batch):
    return sha256(json.dumps(goods_group_parameters(batch),sort_keys=True).encode()).hexdigest()


def mark_calculated(batch):
    batch.needs_recalculation=False
    batch.source_metadata={**batch.source_metadata,'directory_signature':directory_signature(batch)}


def parameters(batch,row,overrides=True):
    result={'volume':row.source_data.get('volume'),'weight_kg':row.source_data.get('weight'),'brand':row.brand}
    for key in FREIGHT_FIELDS:result[key]=batch.parameters.get(key)
    classification=row.source_data.get('classification',{})
    for key in ('planning_sales','group_goods','brand','purch','kind_purch'):
        result[key]=classification.get(key) if 'classification' in row.source_data else row.brand if key=='brand' else None
    result['supply_type']=classification.get('supply_type')
    if overrides:result.update({k:v for k,v in row.overrides.items() if v is not None and k in FIELDS})
    group=goods_group_parameters(batch).get(planning_group_key(result.get('group_goods') or ''),{})
    for key in ('container_volume','duty_percent'):
        result[key]=row.overrides[key] if overrides and row.overrides.get(key) is not None else group.get(key)
    return result


def calculate(batch,row):
    steps=[]
    def step(label,formula,value,currency='USD'):
        steps.append({'label':label,'formula':formula,'value':str(value),'currency':currency})
    def fmt(value):
        return format(Decimal(str(value)).quantize(Decimal('.000001')),'f').rstrip('0').rstrip('.') or '0'
    try:
        if row.source_data.get('error'):raise ValidationError(row.source_data['error'])
        if row.price is None:raise ValidationError('Нет цены PURCH на дату расчёта.')
        if row.currency not in CURRENCIES:raise ValidationError('Не определена поддерживаемая валюта закупочной цены.')
        rates={key:decimal(value,'Курс '+key,optional=True,positive=True) for key,value in batch.rates.items() if key in CURRENCIES};rates['RUB']=Decimal(1)
        price=decimal(row.price,'Цена')
        quantity=Decimal(1)
        step('Цена единицы модели',f'{fmt(row.price)} / {fmt(quantity)}',price,row.currency)
        if row.currency!='USD':
            if not rates.get('USD') or not rates.get(row.currency):raise ValidationError('Для перевода цены в USD заполните курсы USD и '+row.currency+'.')
            original=price
            price=price*rates[row.currency]/rates['USD']
            step('Перевод цены в USD',f'{fmt(original)} × {fmt(rates[row.currency])} / {fmt(rates["USD"])}',price)
        inputs=parameters(batch,row);supply=inputs.get('supply_type')
        if supply not in ('direct','p2','russian'):raise ValidationError('Выберите тип поставки.')
        if supply=='russian':volume,container,duty=Decimal(0),Decimal(1),Decimal(0)
        else:
            if inputs.get('container_volume') is None or inputs.get('duty_percent') is None:
                raise ValidationError('Для группы товаров «'+str(inputs.get('group_goods') or 'не задана')+'» не найдены контейнер и/или пошлина в справочнике «Группы и пошлины». Заполните справочник или задайте значения модели вручную.')
            volume=decimal(inputs.get('volume'),'Объём модели, м³',positive=True)
            container=decimal(inputs.get('container_volume'),'Объём контейнера, м³',positive=True)
            duty=decimal(inputs.get('duty_percent'),'Пошлина, %')/100
            if duty>100:raise ValidationError('Пошлина превышает допустимое значение.')
        good=SimpleNamespace(exw_usd=price,volume=volume,container_volume=container,duty_rate=duty,kddp=None)
        freight=None
        if supply!='russian':
            values={key:decimal(inputs.get(key),key) for key in FREIGHT_FIELDS}
            if values['customs_rate']>100 or values['nr_customs_vat_rate']>100:raise ValidationError('Процентная ставка должна быть от 0 до 100.')
            freight=SimpleNamespace(**values)
        apply_price(good,freight,supply_type=supply)
        if supply=='russian':
            step('DDP закупки РФ','Закупочная цена в USD; логистические расходы не добавляются',good.ddp_usd)
        else:
            ratio=volume/container
            step('Доля контейнера',f'{fmt(volume)} м³ / {fmt(container)} м³',ratio,'')
            step('Фрахт',f'{fmt(values["price_per_container"])} × {fmt(ratio)}',good.freight_usd)
            step('CIF',f'{fmt(price)} + {fmt(good.freight_usd)}',good.cif_usd)
            step('Таможенный платёж',f'{fmt(values["customs_rate"])} / 100 × {fmt(good.cif_usd)} × (1 + {fmt(duty*100)} / 100)',good.customs_payment_usd)
            step('Доставка до склада',f'{fmt(values["warehouse_delivery_cost"])} × {fmt(ratio)}',good.warehouse_delivery_usd)
            step('DDP',f'{fmt(good.cif_usd)} + {fmt(good.customs_payment_usd)} + {fmt(good.warehouse_delivery_usd)}',good.ddp_usd)
            step('Загран доставка — отдельно от DDP',f'{fmt(values["foreign_delivery_cost"])} × {fmt(ratio)}',good.foreign_delivery_usd)
            step('НР НДС — отдельно от DDP',f'{fmt(values["nr_customs_vat_rate"])} / 100 × {fmt(good.customs_payment_usd)}' if supply=='p2' else 'Для этого типа поставки не начисляется',good.nr_customs_vat_usd)
        ddp=Decimal(str(good.ddp_usd));converted={'USD':str(ddp)};warnings=[]
        for code in CURRENCIES[1:]:
            if rates.get('USD') and rates.get(code):converted[code]=str((ddp*rates['USD']/rates[code]).quantize(Decimal('.000001')))
            else:converted[code]=None;warnings.append('Нет курса для DDP '+code)
            if converted[code] is not None:step('DDP '+code,f'{fmt(ddp)} USD × {fmt(rates["USD"])} / {fmt(rates[code])}',converted[code],code)
        return {'steps':steps,'status':'partial' if warnings else 'ready','ddp':converted,'components':{'exw_usd':str(price),**{key:None if getattr(good,key) is None else str(getattr(good,key)) for key in CALCULATED_FIELDS}},'warnings':warnings,'calculated_at':timezone.now().isoformat()}
    except (ValidationError,ArithmeticError) as exc:
        return {'steps':steps,'status':'error','error':' '.join(exc.messages) if isinstance(exc,ValidationError) else 'Расчёт вышел за допустимую точность.'}


def source_parameters(source):
    freight=Freight.objects.filter(scenario=source).first()
    if not freight:raise ValidationError('В выбранном сценарии не заполнены параметры фрахта.')
    result={key:str(getattr(freight,key)) for key in FREIGHT_FIELDS}
    return {'parameters':result,'source':{'id':source.pk,'name':source.name,'copied_at':timezone.now().isoformat()}}


def preview_token(batch,source,user):
    data=source_parameters(source)
    return data,signing.dumps({'identity':[batch.pk,batch.revision,user.pk],**data},salt=SALT,compress=True)


def same_parameter(key,left,right):
    if key in ('volume','weight_kg','container_volume','duty_percent',*FREIGHT_FIELDS):
        try:return Decimal(str(left))==Decimal(str(right))
        except (InvalidOperation,ValueError):return False
    return str(left)==str(right)


@transaction.atomic
def mutate(batch_id,revision,action,*,user=None,token=None,values=None,rows=None):
    batch=ModelDDPCalculation.objects.select_for_update().get(pk=batch_id)
    if str(batch.revision)!=str(revision):raise ValidationError('Расчёт изменён в другой вкладке. Обновите страницу.')
    if action=='workflow_calculate':
        # Compare submitted row values against the old effective defaults BEFORE changing globals.
        # Otherwise unchanged inherited values become overrides and defeat the new global settings.
        changed={}
        for pk,data in (rows or {}).items():
            row=batch.rows.filter(pk=pk).first()
            if not row:raise ValidationError('Строка не принадлежит выбранному расчёту.')
            current=parameters(batch,row)
            changed[pk]={key:value for key,value in data.items() if key in FIELDS and not same_parameter(key,value,current.get(key))}
        rows=changed
        batch.parameters={**batch.parameters,**values['parameters']};batch.rates=values['rates']
    if action=='copy':
        try:data=signing.loads(token,salt=SALT,max_age=3600)
        except signing.BadSignature:raise ValidationError('Предпросмотр устарел. Повторите выбор сценария.')
        if data.get('identity')!=[batch.pk,batch.revision,user.pk]:raise ValidationError('Предпросмотр относится к другому расчёту или ревизии.')
        batch.parameters=data['parameters'];batch.source_metadata=data['source'];batch.source_scenario=ScenarioModel.all_objects.get(pk=data['source']['id'])
    elif action=='parameters':
        batch.parameters={**batch.parameters,**values['parameters']};batch.rates=values['rates']
    elif action=='load':
        previous={(r.model_ref,r.price_type_ref,r.currency):r.overrides for r in batch.rows.all()}
        records=[]
        for data in rows:
            entry=deepcopy(data);entry['overrides']=previous.get((entry['model_ref'],entry['price_type_ref'],entry['currency']),{})
            records.append(ModelDDPRow(calculation=batch,**entry))
        batch.rows.all().delete();ModelDDPRow.objects.bulk_create(records,batch_size=200);batch.loaded_at=timezone.now()
    elif action in ('rows','rows_save','workflow_calculate'):
        for pk,data in rows.items():
            row=batch.rows.filter(pk=pk).first()
            if not row:raise ValidationError('Строка не принадлежит выбранному расчёту.')
            base=parameters(batch,row,False)
            if action=='workflow_calculate':
                new_overrides=deepcopy(row.overrides)
                for key,value in data.items():
                    if value is None or same_parameter(key,value,base.get(key)):new_overrides.pop(key,None)
                    else:new_overrides[key]=value
            else:
                new_overrides={key:value for key,value in data.items() if key in FIELDS and value is not None and not same_parameter(key,value,base.get(key))}
            if action=='rows_save' and new_overrides!=row.overrides:
                batch.needs_recalculation=True
                if row.result:row.result={**row.result,'stale':True}
            row.overrides=new_overrides
            row.save(update_fields=['overrides','result'])
    elif action!='calculate':raise ValidationError('Неизвестное действие.')
    batch.revision+=1
    if action in ('calculate','rows','parameters','workflow_calculate'):
        updates=[]
        for row in batch.rows.all().iterator(chunk_size=200):
            row.result=calculate(batch,row);updates.append(row)
            if len(updates)==200:ModelDDPRow.objects.bulk_update(updates,['result']);updates=[]
        if updates:ModelDDPRow.objects.bulk_update(updates,['result'])
        batch.calculated_at=timezone.now();mark_calculated(batch)
    elif action!='rows_save':
        batch.rows.update(result={});batch.calculated_at=None;batch.needs_recalculation=True
    batch.save()
    return batch
