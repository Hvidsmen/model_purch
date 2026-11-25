"""Preview and apply physical parameters or reverse-priced EXW."""
from copy import copy
from decimal import Decimal
import hashlib,json,math
from django.core.exceptions import ValidationError
from django.db import transaction
from ..models import PGGoods,Freight,Purch,ScenarioModel
from ..goods_identity import planning_group_key
from .pricing import apply_price,number
from ..conns import connect_database


def input_digest(scenario,goods):
    snapshot={'scenario':[scenario.pk,str(scenario.date_start_plan),str(scenario.date_end_plan),str(scenario.plan_parameters_loaded_at)],
              'goods':list(goods.order_by('pk').values()),
              'freight':list(Freight.objects.filter(scenario=scenario).values()),
              'suppliers':list(Purch.objects.order_by('pk').values('name_key','supply_type'))}
    return hashlib.sha256(json.dumps(snapshot,sort_keys=True,default=str).encode()).hexdigest()


def physical_source(connector=None):
    connection,cursor=(connector or connect_database)('vm-dwh','DataWH')
    try:
        connection.timeout=120
        cursor.execute("SELECT name FROM sys.columns WHERE object_id=OBJECT_ID(N'erp.[Справочники.Номенклатура]')")
        columns={str(row[0]).casefold():str(row[0]) for row in cursor.fetchall()}
        def field(*options):
            found=next((columns[v.casefold()] for v in options if v.casefold() in columns),None)
            return '['+found.replace(']',']]')+']' if found else None
        group=field('Даичи_ГруппаПланирования');volume=field('ОбъемЧислитель');weight=field('ВесЧислитель','Вес','Weight')
        if not group:raise ValidationError('В ERP не найдено поле плановой группы.')
        def average(name):return f'AVG(CASE WHEN TRY_CONVERT(float,{name})>0 THEN TRY_CONVERT(float,{name}) END)' if name else 'NULL'
        cursor.execute(f'SELECT {group},{average(volume)},{average(weight)} FROM erp.[Справочники.Номенклатура] WHERE {group} IS NOT NULL GROUP BY {group}')
        result={}
        for name,v,w in cursor.fetchall():
            key=planning_group_key(name)
            if key in result:raise ValidationError('Неоднозначные названия групп в ERP. Уточните справочник.')
            result[key]={'volume':v,'weight_kg':w}
        return result
    finally:connection.close()


def reverse_exw(good,target,freight,supply_type):
    target=number(target,'DDP',positive=True)
    if supply_type!='russian':
        if freight is None:raise ValidationError('Не заданы параметры фрахта сценария.')
        number(good.volume,'Объём товара',positive=True)
    zero=copy(good);zero.exw_usd=0;apply_price(zero,freight,supply_type=supply_type)
    one=copy(good);one.exw_usd=1;apply_price(one,freight,supply_type=supply_type)
    slope=Decimal(str(one.ddp_usd))-Decimal(str(zero.ddp_usd))
    if slope<=0:raise ValidationError('Не удалось определить зависимость DDP от EXW.')
    value=(target-Decimal(str(zero.ddp_usd)))/slope
    if not value.is_finite() or value<0:raise ValidationError('DDP ниже затрат на доставку: неотрицательный EXW невозможен.')
    check=copy(good);check.exw_usd=float(value);apply_price(check,freight,supply_type=supply_type)
    if abs(Decimal(str(check.ddp_usd))-target)>max(Decimal('.000001'),target*Decimal('.000000001')):
        raise ValidationError('Обратный расчёт не сходится с DDP. Проверьте параметры логистики.')
    return float(value)


def scope_goods(scenario,mode,ids):
    goods=PGGoods.objects.filter(scenario_plan=scenario)
    if mode not in ('all','visible','missing'):raise ValidationError('Выберите режим заполнения.')
    if mode=='visible':
        try:ids=set(int(v) for v in ids)
        except (TypeError,ValueError):raise ValidationError('Некорректный список товаров.')
        if not ids or set(goods.filter(pk__in=ids).values_list('pk',flat=True))!=ids:raise ValidationError('Отметьте товары текущего сценария.')
        goods=goods.filter(pk__in=ids)
    return goods


def preview(scenario,source,mode,ids,connector=None):
    if source not in ('physical','plan','fact'):raise ValidationError('Неизвестный источник.')
    goods=scope_goods(scenario,mode,ids);digest=input_digest(scenario,goods)
    if source!='physical' and not scenario.plan_parameters_loaded_at:raise ValidationError('Сначала обновите параметры из плана: нужны загруженные DDP и количества.')
    physical=physical_source(connector) if source=='physical' else {}
    freight=Freight.objects.filter(scenario=scenario).first()
    suppliers=dict(Purch.objects.values_list('name_key','supply_type'))
    rows=[];updates=[]
    for good in goods.order_by('pk'):
        values={};notes=[];target=None;ddp_after=None
        try:
            if source=='physical':
                found=physical.get(good.planning_group_key,{})
                for field,label in (('volume','Объём'),('weight_kg','Масса')):
                    if mode=='missing' and getattr(good,field)!=0:continue
                    value=found.get(field)
                    if value is None or not math.isfinite(float(value)) or float(value)<=0:notes.append(label+': нет положительного значения в ERP');continue
                    if float(value)!=getattr(good,field):values[field]=float(value)
            else:
                if mode=='missing' and good.exw_usd!=0:continue
                quantity=getattr(good,source+'_quantity');target=getattr(good,source+'_ddp_usd')
                if quantity is None or quantity<=0 or target is None or target<=0:raise ValidationError('Нет положительного DDP и количества выбранного источника.')
                values['exw_usd']=reverse_exw(good,target,freight,suppliers.get(planning_group_key(good.purch or '')))
                if values['exw_usd']==good.exw_usd:values={}
            if values:
                check=copy(good)
                for field,value in values.items():setattr(check,field,value)
                apply_price(check,freight,supply_type=suppliers.get(planning_group_key(good.purch or '')))
                ddp_after=check.ddp_usd
                updates.append({'id':good.pk,'values':values})
        except ValidationError as exc:notes.extend(exc.messages);values={}
        if values or notes:
            rows.append({'id':good.pk,'name':good.planning_group,'old_volume':good.volume,'new_volume':values.get('volume',good.volume),'old_weight':good.weight_kg,'new_weight':values.get('weight_kg',good.weight_kg),'old_exw':good.exw_usd,'new_exw':values.get('exw_usd',good.exw_usd),'target':target,'ddp_after':ddp_after,'notes':notes})
    return {'rows':rows,'updates':updates,'digest':digest,'ids':list(goods.order_by('pk').values_list('pk',flat=True))}


@transaction.atomic
def apply(scenario_id,payload):
    scenario=ScenarioModel.objects.select_for_update().get(pk=scenario_id)
    goods=PGGoods.objects.filter(scenario_plan=scenario,pk__in=payload['ids'])
    list(goods.select_for_update())
    if input_digest(scenario,goods)!=payload['digest']:raise ValidationError('Товары, DDP или логистика изменились. Повторите предварительный просмотр.')
    allowed={'volume','weight_kg'} if payload['source']=='physical' else {'exw_usd'}
    count=0
    for update in payload['updates']:
        good=goods.get(pk=update['id'])
        if not set(update['values'])<=allowed:raise ValidationError('Некорректные поля заполнения.')
        for field,value in update['values'].items():setattr(good,field,float(number(value,field)))
        good.save();count+=1
    return count
