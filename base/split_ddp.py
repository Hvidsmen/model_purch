"""Explicit, signed DDP proposals. Preview never changes the scenario."""
from datetime import date,timedelta
from decimal import Decimal,ROUND_HALF_UP
from hashlib import sha256
import json,logging
from zoneinfo import ZoneInfo
from django.core import signing
from django.core.exceptions import ValidationError
from django.utils import timezone
from model_purch.models import ScenarioModel,PGGoods
from model_purch.conns import connect_database
from .sales_planning_scope import PRODUCT_GROUP
from .sales_planning_group_service import group_key
from .split_engine import number,stored,precise,ZERO,STEP
from .split_history import normalized

SALT='split-ddp-v1'
SQL='''SELECT p.PlanningGroupOZPErp,p.ModelCode,SUM(s.Quantity),SUM(s.AmountExwAEUSD),
 SUM(CASE WHEN s.Quantity IS NULL OR s.AmountExwAEUSD IS NULL THEN 1 ELSE 0 END)
 FROM DataWH.dbo.Sales s JOIN DataWH.dbo.Product p ON p.ProductCode=s.ProductCode
 WHERE s.DateInvoice>=? AND s.DateInvoice<? AND p.GroupERP=?
 GROUP BY p.PlanningGroupOZPErp,p.ModelCode'''


STOCK_SQL="""SELECT m.PlanningGroupOZPErp,n.[Наименование],
 SUM(CASE WHEN r.[ВидДвижения]=0 THEN 1 ELSE -1 END*r.[Количество]),
 SUM(CASE WHEN r.[ВидДвижения]=0 THEN 1 ELSE -1 END*(r.[ДопРасходыUSD]+r.[СуммаUSD])),
 SUM(CASE WHEN r.[Количество] IS NULL OR r.[ДопРасходыUSD] IS NULL OR r.[СуммаUSD] IS NULL THEN 1 ELSE 0 END)
 FROM DataWH.erp.[РегистрыНакопления.Даичи_ПервичнаяСебестоимость] r
 INNER JOIN DataWH.erp.[Справочник.Склады] store ON store.[Ссылка]=r.[МестоХраненияСсылка]
 INNER JOIN DataWH.erp.[Справочники.Номенклатура] n ON n.[Ссылка]=r.[НоменклатураСсылка]
 INNER JOIN (SELECT DISTINCT ModelCode,PlanningGroupOZPErp FROM DataWH.dbo.ModelCodeERP WHERE GroupERP=?) m
 ON m.ModelCode=n.[Наименование]
 WHERE CAST(r.[Период] AS date)<=?
 AND LOWER(LTRIM(RTRIM(store.[даичи_НазначениеСклада])))=N'продажа'
 AND NOT EXISTS (SELECT 1 FROM DataWH.erp.[Справочники.ДоговорыКонтрагентов] d
 WHERE d.[Ссылка]=r.[МестоХраненияСсылка] AND d.[ТипДоговора]=N'СХранителем')
 GROUP BY m.PlanningGroupOZPErp,n.[Наименование]"""


def stock(scenario):
    connection=cursor=None
    try:
        connection,cursor=connect_database('vm-dwh','DataWH');connection.timeout=120
        cursor.execute(STOCK_SQL,PRODUCT_GROUP,scenario.stock_date)
        rows=cursor.fetchall()
    except Exception:
        logging.getLogger(__name__).exception('DDP stock source failed')
        raise ValidationError('Не удалось получить DDP из остатков. План не изменён.')
    finally:
        try:
            if cursor is not None:cursor.close()
        finally:
            if connection is not None:connection.close()
    result={}
    for group,model,qty,cost,unknown in rows:
        entry=result.setdefault((group_key(group),normalized(model)),{'qty':ZERO,'cost':ZERO,'unknown':False})
        try:
            values=[None if value is None else Decimal(str(value)) for value in (qty,cost)]
            if any(value is not None and not value.is_finite() for value in values):raise ValueError
        except (ValueError,ArithmeticError):raise ValidationError('В остатках некорректная себестоимость или количество. План не изменён.')
        entry['unknown'] |= bool(unknown) or any(value is None for value in values)
        for field,value in zip(('qty','cost'),values):
            if value is not None:entry[field]+=value
    return result,{'source':'DDP из остатков: (ДопРасходыUSD + СуммаUSD) / количество; сумма стоимости внутреннего и наружного блоков',
                   'stock_date':str(scenario.stock_date),'through':str(scenario.stock_date),'warehouse_purpose':'Продажа',
                   'loaded_at':timezone.now().isoformat()}


def rounded(value):
    if not value.is_finite() or value<=0 or value>Decimal('999999999999'):raise ValidationError('Стоимость выходит за допустимый диапазон.')
    try:value=value.quantize(STEP,rounding=ROUND_HALF_UP)
    except ArithmeticError:raise ValidationError('Стоимость выходит за допустимую точность.')
    number(value)
    if value<=0:raise ValidationError('Стоимость должна быть положительной.')
    return stored(value)


def purchase_price(value):
    try:price=Decimal(str(value))
    except (ValueError,ArithmeticError):raise ValidationError('В закупках нет корректного итогового DDP.')
    if not price.is_finite() or price<=0:raise ValidationError('В закупках не рассчитан положительный итоговый DDP.')
    return Decimal(rounded(price))


def fingerprint(group):
    return sha256(json.dumps({'ref':group['ref'],'powers':group['powers']},sort_keys=True).encode()).hexdigest()


def catalog(scenario,purchase_id=None):
    goods=[]
    if purchase_id:
        if not str(purchase_id).isdecimal():raise ValidationError('Выберите сценарий закупок из списка.')
        purchase=ScenarioModel.objects.filter(pk=purchase_id).first()
        if not purchase:raise ValidationError('Выберите действующий сценарий закупок.')
        goods=[{'id':str(g.pk),'name':g.planning_group,'ddp':None if g.ddp_usd is None else str(g.ddp_usd)}
               for g in PGGoods.objects.filter(scenario_plan=purchase).order_by('planning_group','pk')]
    return {'goods':goods,'groups':[{'id':gid,'name':g['ref']['name'],
        'powers':[key for key,p in g['powers'].items() if p['active']],
        'matches':[r['id'] for r in goods if group_key(r['name'])==g['ref']['source_key']]}
        for gid,g in scenario.state['groups'].items()]}


def fact(year):
    today=timezone.localdate(timezone=ZoneInfo('Europe/Moscow'))
    if not isinstance(year,int) or year<2000 or year>today.year:raise ValidationError('Выберите календарный год факта до текущего включительно.')
    end=min(date(year+1,1,1),today+timedelta(days=1));connection=cursor=None
    try:
        connection,cursor=connect_database('vm-dwh','DataWH');connection.timeout=60
        cursor.execute(SQL,date(year,1,1),end,PRODUCT_GROUP)
        rows=cursor.fetchall()
    except Exception:
        logging.getLogger(__name__).exception('DDP yearly fact failed')
        raise ValidationError('Не удалось получить годовой факт DDP. План не изменён.')
    finally:
        try:
            if cursor is not None:cursor.close()
        finally:
            if connection is not None:connection.close()
    result={}
    for group,model,qty,cost,unknown in rows:
        key=(group_key(group),normalized(model))
        entry=result.setdefault(key,{'qty':ZERO,'cost':ZERO,'unknown':False})
        try:
            values=[None if v is None else Decimal(str(v)) for v in (qty,cost)]
            if any(v is not None and not v.is_finite() for v in values):raise ValueError
        except (ValueError,ArithmeticError):raise ValidationError('В факте некорректные суммы или количества. План не изменён.')
        entry['unknown'] |= bool(unknown) or any(v is None for v in values)
        for field,value in zip(('qty','cost'),values):
            if value is not None:entry[field]+=value
    return result,{'loaded_at':timezone.now().isoformat(),'year':year,'from':f'{year}-01-01','through':str(end-timedelta(days=1)),
                   'source':'DataWH.dbo.Sales.AmountExwAEUSD / Quantity; сумма DDP внутреннего и наружного блоков'}


def weighted_powers(group):
    powers={}
    for key,power in group['powers'].items():
        share=number(power.get('share'),optional=True)
        if share is None:raise ValidationError('Сначала задайте доли мощностей.')
        if share>0:
            if not power['active']:raise ValidationError('В плане есть неактивная мощность: '+key)
            powers[key]=(power,share)
    if sum((share for _,share in powers.values()),ZERO)!=100:raise ValidationError('Сумма долей мощностей должна быть 100%.')
    return powers


@precise
def preview(scenario,post):
    try:targets=json.loads(post.get('targets','[]'));mapping=json.loads(post.get('mapping','{}'))
    except (ValueError,TypeError):raise ValidationError('Некорректный выбор групп.')
    if not isinstance(targets,list) or not targets or len(targets)!=len(set(map(str,targets))) or any(not isinstance(gid,str) or gid not in scenario.state['groups'] for gid in targets) or not isinstance(mapping,dict):raise ValidationError('Выберите группы текущего сценария.')
    source=post.get('source');base={};purchase_goods={};markup=ZERO
    if source=='fact':
        try:year=int(post.get('year',''))
        except (ValueError,TypeError):raise ValidationError('Выберите год факта.')
        markup=number(post.get('surcharge','0'),maximum=Decimal(10000))
        base,metadata=fact(year);metadata['surcharge_percent']=stored(markup)
    elif source=='stock':
        base,metadata=stock(scenario)
    elif source=='purchase':
        purchase=ScenarioModel.objects.filter(pk=post.get('purchase') if str(post.get('purchase','')).isdecimal() else 0).first()
        if not purchase:raise ValidationError('Выберите действующий сценарий закупок.')
        purchase_goods={str(g.pk):g for g in PGGoods.objects.filter(scenario_plan=purchase)}
        metadata={'source':'Итоговый DDP модели закупок: доставка, пошлины и прочие расходы включены','purchase_id':purchase.pk,'purchase_title':purchase.name}
    else:raise ValidationError('Выберите источник DDP.')
    rows=[]
    for gid in targets:
        group=scenario.state['groups'][gid]
        row={'gid':gid,'name':group['ref']['name'],'current':group.get('ddp'),'proposed':None,'powers':[],'error':''}
        try:
            parameters={};prices={};details=[];origin=dict(metadata)
            selected=mapping.get(gid,{})
            if source=='purchase' and isinstance(selected,str):
                good=purchase_goods.get(selected)
                if not good:raise ValidationError('Выберите группу из выбранного сценария закупок.')
                proposed=rounded(purchase_price(good.ddp_usd))
                origin.update(goods_id=good.pk,goods_group=good.planning_group,basis='group',note='DDP группы без детализации по мощностям; текущие соотношения мощностей сохранятся.')
            else:
                for key,(power,share) in weighted_powers(group).items():
                    if source in ('fact','stock'):
                        unit={};params={}
                        for kind in ('indoor','outdoor'):
                            candidates=[m for m in power['models'] if m['kind']==kind]
                            model=next((m for m in candidates if m['id']==power.get(kind)),None)
                            if model is None and not power.get(kind) and len(candidates)==1:model=candidates[0]
                            if model is None:raise ValidationError(key+': выберите однозначную пару моделей.')
                            entry=base.get((group['ref']['source_key'],normalized(model['name'])))
                            if not entry or entry['unknown'] or entry['qty']<=0 or entry['cost']<=0:raise ValidationError(key+': нет положительного DDP и количества для '+model['name'])
                            unit[kind]=entry['cost']/entry['qty'];params[kind]=model['id']
                        cost=sum(unit.values(),ZERO)*(1+markup/100)
                        params['indoor_cost_percent']=rounded(unit['indoor']/sum(unit.values(),ZERO)*100)
                        parameters[key]=params
                    else:
                        good=purchase_goods.get(selected.get(key)) if isinstance(selected,dict) else None
                        if not good:raise ValidationError(key+': сопоставьте мощность с группой закупок или выберите DDP всей группы.')
                        cost=purchase_price(good.ddp_usd)
                        details.append({'power':key,'goods_id':good.pk,'group':good.planning_group})
                    prices[key]=cost
                    row['powers'].append({'power':key,'ddp':rounded(cost),'share':stored(share)})
                proposed=rounded(sum((prices[key]*share/100 for key,(_,share) in weighted_powers(group).items()),ZERO))
                for key,cost in prices.items():parameters.setdefault(key,{})['factor']=rounded(cost/min(prices.values()))
                origin.update(basis='powers',mapping=details)
            origin.update(quoted_at=timezone.now().isoformat(),group_ddp=proposed,power_ddp=row['powers'])
            row.update(proposed=proposed,source=origin)
            signed={'scenario':scenario.pk,'revision':scenario.revision,'gid':gid,'fingerprint':fingerprint(group),'ddp':proposed,'parameters':parameters,'source':origin}
            row['token']=signing.dumps(signed,salt=SALT,compress=True)
        except ValidationError as exc:row['error']=' '.join(exc.messages)
        rows.append(row)
    return {'rows':rows,'source':metadata}


def apply_pending(scenario,state,post):
    for gid,group in state['groups'].items():
        token=post.get('ddp_proposal_'+gid)
        if token:
            try:signed=signing.loads(token,salt=SALT,max_age=86400)
            except signing.BadSignature:raise ValidationError('Предпросмотр DDP устарел. Получите предложение заново.')
            if signed.get('scenario')!=scenario.pk or signed.get('revision')!=scenario.revision or signed.get('gid')!=gid or signed.get('fingerprint')!=fingerprint(scenario.state['groups'][gid]):raise ValidationError('Состав или доли изменились. Повторите предпросмотр DDP.')
            if number(group.get('ddp'),optional=True)==number(signed['ddp']):
                for key,params in signed['parameters'].items():group['powers'][key].update(params)
                group['ddp_source']=signed['source']
                continue
        if group.get('ddp')!=scenario.state['groups'][gid].get('ddp'):group.pop('ddp_source',None)
