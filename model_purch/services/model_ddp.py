"""Independent per-model DDP snapshots, reusing the purchase pricing formula."""
from copy import deepcopy
from decimal import Decimal,InvalidOperation
from types import SimpleNamespace
from django.core.exceptions import ValidationError
from django.core import signing
from django.db import transaction
from django.utils import timezone
from ..models import ModelDDPCalculation,ModelDDPRow,ScenarioModel,Freight,PGGoods,Purch
from ..goods_identity import planning_group_key
from .pricing import apply_price,CALCULATED_FIELDS
from .model_ddp_source import empty_reference

CURRENCIES=('USD','EUR','CNY','RUB')
MODEL_FIELDS=('container_volume','duty_percent','supply_type','planning_sales','group_goods','brand','purch','kind_purch','stock_cnt_day','percent_stock_end')
FIELDS=('volume','weight_kg','price_unit_confirmed','price_quantity',*MODEL_FIELDS,'price_per_container','customs_rate','warehouse_delivery_cost','foreign_delivery_cost','nr_customs_vat_rate')
FREIGHT_FIELDS=('price_per_container','customs_rate','warehouse_delivery_cost','foreign_delivery_cost','nr_customs_vat_rate')
SALT='purchase-model-ddp-parameters-v1'


def decimal(value,label,optional=False,positive=False):
    if optional and value in (None,''):return None
    try:
        value=Decimal(str(value).replace(',','.'))
        if not value.is_finite() or value<0 or value>Decimal('1000000000000') or positive and value==0:raise ValueError
        return value
    except (ValueError,InvalidOperation,TypeError):raise ValidationError(label+': некорректное значение.')


def parameters(batch,row,overrides=True):
    group=batch.parameters.get('groups',{}).get(planning_group_key(row.planning_group),{})
    result={'volume':row.source_data.get('volume'),'weight_kg':row.source_data.get('weight'),'brand':row.brand}
    for key in (*MODEL_FIELDS,*FREIGHT_FIELDS):
        result[key]=group.get(key) if group.get(key) is not None else batch.parameters.get(key,result.get(key))
    if overrides:result.update({k:v for k,v in row.overrides.items() if v is not None})
    return result


def calculate(batch,row):
    steps=[]
    def step(label,formula,value,currency='USD'):
        steps.append({'label':label,'formula':formula,'value':str(value),'currency':currency})
    def fmt(value):
        return format(Decimal(str(value)).quantize(Decimal('.000001')),'f').rstrip('0').rstrip('.') or '0'
    try:
        if row.source_data.get('error'):raise ValidationError(row.source_data['error'])
        if not empty_reference(row.source_data.get('unit')) and not row.overrides.get('price_unit_confirmed',False):raise ValidationError(row.source_data.get('unit_warning','Подтвердите единицу закупочной цены.'))
        if row.price is None:raise ValidationError('Нет цены PURCH на дату расчёта.')
        if row.currency not in CURRENCIES:raise ValidationError('Не определена поддерживаемая валюта закупочной цены.')
        rates={key:decimal(value,'Курс '+key,optional=True,positive=True) for key,value in batch.rates.items() if key in CURRENCIES};rates['RUB']=Decimal(1)
        price=decimal(row.price,'Цена')/decimal(row.overrides.get('price_quantity') or '1','Количество единиц в цене',positive=True)
        quantity=decimal(row.overrides.get('price_quantity') or '1','Количество единиц в цене',positive=True)
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
    result['container_volume']=str(freight.volume_per_container);result['groups']={}
    suppliers=dict(Purch.objects.values_list('name_key','supply_type'))
    for good in PGGoods.objects.filter(scenario_plan=source).select_related('kind_purch'):
        key=planning_group_key(good.planning_group)
        if key in result['groups']:raise ValidationError('В источнике неоднозначные параметры группы '+good.planning_group)
        result['groups'][key]={'name':good.planning_group,'container_volume':str(good.container_volume),'duty_percent':str(Decimal(str(good.duty_rate))*100),'supply_type':suppliers.get(planning_group_key(good.purch or '')),**{k:getattr(good,k) for k in ('planning_sales','group_goods','brand','purch','stock_cnt_day','percent_stock_end')},'kind_purch':str(good.kind_purch)}
    return {'parameters':result,'source':{'id':source.pk,'name':source.name,'copied_at':timezone.now().isoformat()}}


def preview_token(batch,source,user):
    data=source_parameters(source)
    return data,signing.dumps({'identity':[batch.pk,batch.revision,user.pk],**data},salt=SALT,compress=True)


@transaction.atomic
def mutate(batch_id,revision,action,*,user=None,token=None,values=None,rows=None):
    batch=ModelDDPCalculation.objects.select_for_update().get(pk=batch_id)
    if str(batch.revision)!=str(revision):raise ValidationError('Расчёт изменён в другой вкладке. Обновите страницу.')
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
    elif action=='rows':
        for pk,data in rows.items():
            row=batch.rows.filter(pk=pk).first()
            if not row:raise ValidationError('Строка не принадлежит выбранному расчёту.')
            base=parameters(batch,row,False)
            row.overrides={key:value for key,value in data.items() if key in FIELDS and value is not None and str(value)!=str(base.get(key))}
            row.save(update_fields=['overrides'])
    elif action!='calculate':raise ValidationError('Неизвестное действие.')
    batch.revision+=1
    if action in ('calculate','rows','parameters'):
        updates=[]
        for row in batch.rows.all().iterator(chunk_size=200):
            row.result=calculate(batch,row);updates.append(row)
            if len(updates)==200:ModelDDPRow.objects.bulk_update(updates,['result']);updates=[]
        if updates:ModelDDPRow.objects.bulk_update(updates,['result'])
        batch.calculated_at=timezone.now()
    else:
        batch.rows.update(result={});batch.calculated_at=None
    batch.save()
    return batch
