"""Explicit sales quantity/markup initialization from source data and saved forecast."""
from calendar import monthrange
from copy import deepcopy
from datetime import date,timedelta
from decimal import Decimal,ROUND_HALF_UP
import json
from zoneinfo import ZoneInfo
from django.core.exceptions import ValidationError
from django.utils import timezone
from .split_engine import ZERO,STEP,allocate,number,stored,precise
from .split_history import normalized
from .sales_planning_group_service import group_key
from .sales_planning_scope import PRODUCT_GROUP
from .split_forecast_plan import query,VERSIONS_SQL
from .split_preplan_forecast import resolve
from .split_group_plan import set_period_quantity

FACT_SQL='''SELECT p.PlanningGroupOZPErp,d.Chanel,p.ModelCode,SUM(s.Quantity),
 SUM(s.Price-s.AmountUSDIt_ITW),SUM(s.AmountExwAEUSD),
 SUM(CASE WHEN s.Quantity IS NULL OR s.Price IS NULL OR s.AmountUSDIt_ITW IS NULL OR s.AmountExwAEUSD IS NULL THEN 1 ELSE 0 END)
 FROM DataWH.dbo.Sales s JOIN DataWH.dbo.Product p ON p.ProductCode=s.ProductCode
 JOIN DataWH.dbo.Subdivisions d ON d.SubdivisionKey=s.Subdivision
 WHERE s.DateInvoice>=? AND s.DateInvoice<? AND p.GroupERP=? AND p.[Тип номенклатуры]=N'Товар'
 AND d.Chanel IN (__CHANNELS__) GROUP BY p.PlanningGroupOZPErp,d.Chanel,p.ModelCode'''
ERP_SQL='''WITH calendar AS (
 SELECT CalendarYear,MonthNumberOfYear,COUNT(*) work_days,
 SUM(CASE WHEN FullDateAlternateKey BETWEEN ? AND ? THEN 1 ELSE 0 END) selected_days
 FROM DataWH.dbo.Date WHERE IsWorkDay=1 GROUP BY CalendarYear,MonthNumberOfYear
)
 SELECT m.PlanningGroupOZPErp,d.Chanel,n.[Наименование],
 SUM(CAST(p.[Количество] AS decimal(28,12))*c.selected_days/c.work_days),
 SUM(CAST(p.[Сумма] AS decimal(28,12))*c.selected_days/c.work_days),
 SUM(CAST(p.[даичи_СуммаДДП] AS decimal(28,12))*c.selected_days/c.work_days),
 SUM(CASE WHEN p.[Количество] IS NULL OR p.[Сумма] IS NULL OR p.[даичи_СуммаДДП] IS NULL THEN 1 ELSE 0 END)
 FROM DataWH.erp.[РегистрыНакопления.ПланыПродаж] p
 JOIN DataWH.erp.[Справочники.Номенклатура] n ON n.[Ссылка]=p.[НоменклатураСсылка]
 JOIN (SELECT DISTINCT ModelCode,PlanningGroupOZPErp FROM DataWH.dbo.ModelCodeERP WHERE GroupERP=?) m ON m.ModelCode=n.[Наименование]
 JOIN DataWH.dbo.Subdivisions d ON d.SubdivisionKey=p.[Подразделение]
 JOIN calendar c ON c.CalendarYear=YEAR(p.[Период]) AND c.MonthNumberOfYear=MONTH(p.[Период])
 WHERE p.[Сценарий]=? AND p.[Период]>=? AND p.[Период]<? AND c.selected_days>0 AND d.Chanel IN (__CHANNELS__)
 GROUP BY m.PlanningGroupOZPErp,d.Chanel,n.[Наименование]'''


def selection(scenario,post):
    try:ids=json.loads(post.get('groups','[]'));cids=json.loads(post.get('channels','[]'))
    except (ValueError,TypeError):raise ValidationError('Выберите группы и каналы.')
    for values,allowed in [(ids,scenario.state['groups']),(cids,scenario.state['channels'])]:
        if not isinstance(values,list) or not values or any(not isinstance(v,str) or v not in allowed for v in values) or len(set(values))!=len(values):raise ValidationError('Выберите группы и каналы сценария.')
    return ids,cids


def year_value(scenario,post):
    try:year=int(post.get('year',''))
    except (ValueError,TypeError):raise ValidationError('Выберите год источника.')
    if year<2000 or year>9998:raise ValidationError('Год источника должен быть от 2000 до 9998.')
    return year


def source(scenario,method,year,version):
    channels=sorted({ch['name'] for ch in scenario.state['channels'].values()})
    slots=','.join('?' for _ in channels)
    if method=='history_forecast':
        start,end=date(year,1,1),scenario.stock_date
        rows=query(FACT_SQL.replace('__CHANNELS__',slots),[start,end+timedelta(days=1),PRODUCT_GROUP,*channels])
    else:
        def shifted(day):
            y=year+day.year-scenario.start.year
            return date(y,day.month,min(day.day,monthrange(y,day.month)[1]))
        start,end=shifted(scenario.start),shifted(scenario.end)
        rows=query(ERP_SQL.replace('__CHANNELS__',slots),[start,end,PRODUCT_GROUP,version,start.replace(day=1),(end.replace(day=1)+timedelta(days=32)).replace(day=1),*channels])
    result={}
    for group,channel,model,qty,revenue,cost,unknown in rows:
        key=(group_key(str(group or '')),normalized(channel),normalized(model))
        entry=result.setdefault(key,{'qty':ZERO,'revenue':ZERO,'cost':ZERO,'unknown':False})
        try:values=[None if value is None else Decimal(str(value)) for value in (qty,revenue,cost)]
        except (ValueError,ArithmeticError):raise ValidationError('В источнике некорректные числовые значения.')
        if any(value is not None and not value.is_finite() for value in values):raise ValidationError('В источнике некорректные числовые значения.')
        entry['unknown'] |= bool(unknown) or any(value is None for value in values)
        for field,value in zip(('qty','revenue','cost'),values):
            if value is not None:entry[field]+=value
    return result,{'from':str(start),'through':str(end),'method':method,'version':version,'year':year,'loaded_at':timezone.now().isoformat()}


def versions(scenario,post):
    year=year_value(scenario,post);channels=sorted({ch['name'] for ch in scenario.state['channels'].values()})
    if not channels:raise ValidationError('Добавьте каналы продаж.')
    return [str(row[0]) for row in query(VERSIONS_SQL.replace('__CHANNELS__',','.join('?' for _ in channels)),[PRODUCT_GROUP,date(year,1,1),date(year+1,1,1),*channels])]


def channel_fact(group,cname,records):
    mapping={}
    for power,p in group['powers'].items():
        for model in p['models']:
            key=normalized(model['name']);identity=(power,model['kind'])
            if key in mapping and mapping[key]!=identity:raise ValidationError('Неоднозначное сопоставление модели и мощности.')
            mapping[key]=identity
    matching=[(model,entry) for (gid,ch,model),entry in records.items() if gid==group['ref']['source_key'] and ch==normalized(cname)]
    if not matching:raise ValidationError('Нет данных по группе и каналу в источнике.')
    powers={};revenue=cost=ZERO
    for model,entry in matching:
        if entry['unknown']:raise ValidationError('В источнике неполные показатели количества или стоимости.')
        if model not in mapping:
            if any(entry[field] for field in ('qty','revenue','cost')):raise ValidationError('Модель из источника отсутствует в снимке группы.')
            continue
        power,kind=mapping[model]
        if kind not in ('indoor','outdoor'):continue
        p=powers.setdefault(power,{'indoor':ZERO,'outdoor':ZERO});p[kind]+=entry['qty']
        revenue+=entry['revenue'];cost+=entry['cost']
    qty=sum((min(max(p['indoor'],ZERO),max(p['outdoor'],ZERO)) for p in powers.values()),ZERO)
    return {'qty':qty,'markup':revenue/cost if cost>0 and revenue>0 else None}


@precise
def calculate(scenario,post):
    ids,cids=selection(scenario,post);method=post.get('method');year=year_value(scenario,post)
    if method not in ('history_forecast','erp'):raise ValidationError('Выберите источник плана.')
    fill=post.get('fill','append')
    if fill not in ('append','replace'):raise ValidationError('Выберите режим заполнения.')
    qcoef=number(post.get('quantity_coefficient'),maximum=Decimal(1000));mcoef=number(post.get('markup_coefficient'),maximum=Decimal(1000))
    if mcoef<=0:raise ValidationError('Коэффициент наценки должен быть положительным.')
    version=post.get('version','').strip()
    if method=='erp' and (not version or len(version)>255):raise ValidationError('Выберите версию плана ERP.')
    if method=='history_forecast' and (scenario.start!=date(scenario.start.year,1,1) or scenario.end!=date(scenario.start.year,12,31) or year!=scenario.start.year-1 or scenario.stock_date.year!=year):raise ValidationError('Факт + прогноз доступен для полного календарного плана: год источника и дата стока должны относиться к предыдущему году.')
    if method=='history_forecast' and scenario.stock_date>timezone.localdate(timezone=ZoneInfo('Europe/Moscow')):
        raise ValidationError('Дата стока ещё не наступила: факт предыдущего года неполный.')
    records,origin=source(scenario,method,year,version)
    state=deepcopy(scenario.state);saved=0;skipped=[]
    for gid in ids:
        group=state['groups'][gid];original=scenario.state['groups'][gid];bases={};errors={}
        for cid,ch in scenario.state['channels'].items():
            try:bases[cid]=channel_fact(group,ch['name'],records)
            except ValidationError as exc:
                has_group_data=any(key[0]==group['ref']['source_key'] for key in records)
                if method=='history_forecast' and has_group_data and not any(key[0]==group['ref']['source_key'] and key[1]==normalized(ch['name']) for key in records):
                    bases[cid]={'qty':ZERO,'markup':None}
                else:errors[cid]=' '.join(exc.messages)
        additions={};forecast_error=''
        if method=='history_forecast':
            try:
                if errors:raise ValidationError('Для распределения прогноза нужен факт всех каналов группы. '+ '; '.join(errors.values()))
                forecast=ZERO if scenario.stock_date==date(year,12,31) else resolve(scenario,original)['total']
                if forecast is None:raise ValidationError('Не заполнен прогноз до начала плана на шаге 4.')
                weights=[bases[cid]['qty'] for cid in scenario.state['channels']]
                if forecast>0 and not sum(weights,ZERO):raise ValidationError('Нет положительных продаж для распределения прогноза по каналам.')
                additions=dict(zip(scenario.state['channels'],allocate(forecast,weights) if forecast else [ZERO]*len(weights)))
            except ValidationError as exc:forecast_error=' '.join(exc.messages)
        for cid in cids:
            line=group['channels'][cid];name=group['ref']['name']+' · '+scenario.state['channels'][cid]['name'];changed=[];reasons=[]
            if cid not in bases:skipped.append({'name':name,'reason':errors[cid]});continue
            base=bases[cid]
            for field,lock,coef in [('input','lock_qty',qcoef),('markup','lock_markup',mcoef)]:
                if line.get(lock):reasons.append(('Количество' if field=='input' else 'Наценка')+': закреплено.');continue
                if fill=='append' and number(line.get(field),optional=True) not in (None,ZERO):continue
                try:
                    if field=='input':
                        if forecast_error:raise ValidationError(forecast_error)
                        value=((base['qty']+additions.get(cid,ZERO))*coef).quantize(STEP,rounding=ROUND_HALF_UP)
                        previous=deepcopy(line);candidate=deepcopy(line)
                        set_period_quantity(scenario,state,candidate,previous,value)
                        line.update(candidate)
                        state['sales_reviewed_groups']=sorted(set(state.get('sales_reviewed_groups',[]))|{gid})
                    else:
                        if base['markup'] is None:raise ValidationError('Нет положительной выручки и себестоимости для наценки.')
                        value=(base['markup']*coef).quantize(STEP,rounding=ROUND_HALF_UP);number(value)
                        if value<=0:raise ValidationError('Наценка после коэффициента должна быть положительной.')
                        line[field]=stored(value)
                    changed.append(field)
                except ValidationError as exc:reasons.append(('Количество' if field=='input' else 'Наценка')+': '+' '.join(exc.messages))
            if changed:
                line.setdefault('prefill_sources',{}).update({field:{**origin,'quantity_coefficient':stored(qcoef),'markup_coefficient':stored(mcoef),'fill':fill} for field in changed});saved+=1
            if reasons or not changed:skipped.append({'name':name,'reason':'; '.join(reasons) or 'Заполненные значения сохранены без изменений.'})
    return state,{'count':saved,'skipped':skipped}
