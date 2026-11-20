"""Explicit previews of pre-plan sales, based on the same dates in a prior year."""
from calendar import monthrange
from copy import deepcopy
from datetime import date,timedelta
from decimal import Decimal,ROUND_HALF_UP
import json,logging
from zoneinfo import ZoneInfo
from django.core import signing
from django.core.exceptions import ValidationError
from django.utils import timezone
from model_purch.conns import connect_database
from .sales_planning_scope import PRODUCT_GROUP
from .sales_planning_group_service import group_key
from .split_engine import ZERO,STEP,number,stored,precise,forecast_months
from .split_history import normalized
from .split_preplan_forecast import resolve
from .split_ddp import fingerprint

SALT='split-preplan-history-v1'
SQL='''SELECT p.PlanningGroupOZPErp,p.ModelCode,SUM(s.Quantity),
 SUM(CASE WHEN s.Quantity IS NULL THEN 1 ELSE 0 END)
 FROM DataWH.dbo.Sales s
 JOIN DataWH.dbo.Product p ON p.ProductCode=s.ProductCode
 JOIN DataWH.dbo.Subdivisions d ON d.SubdivisionKey=s.Subdivision
 WHERE s.DateInvoice>=? AND s.DateInvoice<? AND p.GroupERP=?
 AND p.[Тип номенклатуры]=N'Товар' AND d.Chanel IN (__CHANNELS__)
 GROUP BY p.PlanningGroupOZPErp,p.ModelCode'''


def period(scenario,year):
    first=scenario.stock_date+timedelta(days=1);last=scenario.start-timedelta(days=1)
    if first>last:raise ValidationError('Между срезом стока и началом плана нет периода для прогноза.')
    shift=first.year-year
    def moved(day):
        target=day.year-shift
        return date(target,day.month,min(day.day,monthrange(target,day.month)[1]))
    if year<2000 or shift<0:raise ValidationError('Выберите год факта от 2000 до года среза стока.')
    if shift==0:
        end=min(scenario.stock_date,timezone.localdate(timezone=ZoneInfo('Europe/Moscow')))
        start=date(year,1,1)
        if end<start:raise ValidationError('За выбранный год ещё нет периода факта.')
        return first,last,start,end
    start,end=moved(first),moved(last)
    if end>timezone.localdate(timezone=ZoneInfo('Europe/Moscow')):raise ValidationError('Аналогичный период факта ещё не завершён.')
    return first,last,start,end


def source(scenario,start,end):
    channels=sorted({ch['name'] for ch in scenario.state['channels'].values()})
    if not channels:raise ValidationError('Сначала добавьте каналы продаж.')
    connection=cursor=None
    try:
        connection,cursor=connect_database('vm-dwh','DataWH');connection.timeout=120
        cursor.execute(SQL.replace('__CHANNELS__',','.join('?' for _ in channels)),start,end+timedelta(days=1),PRODUCT_GROUP,*channels)
        rows=cursor.fetchall()
    except Exception:
        logging.getLogger(__name__).exception('Split pre-plan forecast history failed')
        raise ValidationError('Не удалось получить историю продаж. Прогноз не изменён.')
    finally:
        try:
            if cursor is not None:cursor.close()
        finally:
            if connection is not None:connection.close()
    result={}
    for group,model,qty,missing in rows:
        identity=(group_key(str(group or '')),normalized(model))
        item=result.setdefault(identity,{'qty':ZERO,'unknown':False})
        try:value=None if qty is None else Decimal(str(qty))
        except (ValueError,ArithmeticError):raise ValidationError('В истории некорректное количество продаж.')
        if value is not None and not value.is_finite():raise ValidationError('В истории некорректное количество продаж.')
        item['unknown'] |= bool(missing) or value is None
        if value is not None:item['qty']+=value
    return result,channels


def has_forecast(scenario,group):
    mode=group.get('forecast_mode','monthly')
    if mode=='group':values=[group.get('forecast_before_total')]
    elif mode=='powers':values=[power.get('forecast_before') for power in group['powers'].values()]
    else:values=[group.get('forecast',{}).get(month) for month in forecast_months(scenario)]
    return any(value is not None and str(value).strip()!='' for value in values)


def needs_stock_repair(scenario,group):
    """Only unsafe automatic forecasts may bypass the fill-empty protection."""
    origin=group.get('forecast_history_source') or {}
    if group.get('forecast_workspace_source') or group.get('forecast_mode')!='powers':return False
    if origin.get('method') not in ('plan_execution','average_daily','same_calendar_dates') and origin.get('source')!='DataWH.dbo.Sales.Quantity':return False
    from .split_forecast_plan import available_before
    try:
        return any(number(power.get('forecast_before'),optional=True) is not None and
                   number(power['forecast_before'])>available_before(power,key) for key,power in group['powers'].items())
    except ValidationError:return False


@precise
def preview(scenario,post):
    if post.get('method')=='plan_execution':
        from .split_forecast_plan import preview as plan_preview
        return plan_preview(scenario,post)
    if post.get('method','history')!='history':raise ValidationError('Выберите метод прогноза.')
    try:targets=json.loads(post.get('targets','[]'));year=int(post.get('year',''))
    except (ValueError,TypeError):raise ValidationError('Выберите группы и год факта.')
    if not isinstance(targets,list) or not targets or any(not isinstance(gid,str) or gid not in scenario.state['groups'] for gid in targets) or len(set(targets))!=len(targets):raise ValidationError('Выберите группы текущего сценария.')
    try:adjustment=Decimal(str(post.get('adjustment','0')).strip().replace(',','.'))
    except (ValueError,ArithmeticError):raise ValidationError('Введите корректировку в процентах.')
    if not adjustment.is_finite() or adjustment< -100 or adjustment>1000 or adjustment.as_tuple().exponent< -6:raise ValidationError('Корректировка должна быть от −100 до 1000%, до 6 знаков после запятой.')
    first,last,start,end=period(scenario,year)
    partial=year==first.year
    target_days=(last-first).days+1;fact_days=(end-start).days+1
    multiplier=Decimal(target_days)/Decimal(fact_days) if partial else Decimal(1)
    facts,channels=source(scenario,start,end)
    metadata={'source':'DataWH.dbo.Sales.Quantity','year':year,'from':str(start),'through':str(end),
              'target_from':str(first),'target_through':str(last),'channels':channels,'adjustment_percent':stored(adjustment),
              'loaded_at':timezone.now().isoformat(),'rule':'daily-net-indoor-outdoor-per-power-v1' if partial else 'min-net-indoor-outdoor-per-power-v1',
              'method':'average_daily' if partial else 'same_calendar_dates','fact_days':fact_days,'target_days':target_days,
              'calculation':'Чистые продажи за '+str(fact_days)+' дн. / '+str(fact_days)+' × '+str(target_days)+' дн. прогноза' if partial else 'Чистые продажи за те же календарные даты прошлого года'}
    rows=[]
    for gid in targets:
        group=scenario.state['groups'][gid];current=resolve(scenario,group)['total']
        row={'gid':gid,'name':group['ref']['name'],'current':None if current is None else stored(current),'has_current':has_forecast(scenario,group),'needs_stock_repair':needs_stock_repair(scenario,group),'proposed':None,'powers':[],'error':''}
        try:
            model_map={}
            for key,power in group['powers'].items():
                for model in power['models']:
                    if model['kind'] not in ('indoor','outdoor'):continue
                    identity=normalized(model['name']);pair=(key,model['kind'])
                    if identity in model_map and model_map[identity]!=pair:raise ValidationError('Модель неоднозначно сопоставлена с мощностью и типом блока: '+model['name'])
                    model_map[identity]=pair
            own={model:entry for (g,model),entry in facts.items() if g==group['ref']['source_key']}
            if not own:raise ValidationError('Нет истории продаж этой группы за выбранный период; ноль автоматически не подставлен.')
            totals={key:{'indoor':ZERO,'outdoor':ZERO} for key in group['powers']}
            for model,entry in own.items():
                if entry['unknown']:raise ValidationError('В истории есть неизвестное количество; прогноз не предложен.')
                if model not in model_map:
                    if entry['qty']!=0:raise ValidationError('Нет сопоставления модели с мощностью: '+model)
                    continue
                key,kind=model_map[model];totals[key][kind]+=entry['qty']
            from .split_forecast_plan import available_before
            values={};warnings=[];requested={};available={}
            for key,counts in totals.items():
                net=min(counts.values())
                if net<0:warnings.append(key+': чистый факт отрицательный; прогноз принят равным 0')
                value=(max(ZERO,net)*multiplier*(1+adjustment/100)).quantize(STEP,rounding=ROUND_HALF_UP)
                number(value);requested[key]=value;available[key]=available_before(group['powers'][key],key)
                limited=min(value,available[key]);values[key]=stored(number(limited))
                if limited<value:warnings.append(key+': прогноз ограничен остатком и поступлениями до начала: '+stored(value)+' → '+stored(limited)+' компл.')
                row['powers'].append({'power':key,'indoor':stored(counts['indoor']),'outdoor':stored(counts['outdoor']),'sets':stored(limited),'requested':stored(value),'available':stored(available[key])})
            if not values:raise ValidationError('В группе нет мощностей для прогноза.')
            proposed=stored(number(sum((Decimal(v) for v in values.values()),ZERO)))
            origin={**metadata,'stock_constraint':'before-start-v1','stock_cap_applied':any(requested[key]>available[key] for key in requested),
                    'unconstrained_forecast':stored(sum(requested.values(),ZERO)),'available_before':{key:stored(value) for key,value in available.items()}}
            row.update(proposed=proposed,warnings=warnings,token=signing.dumps({'scenario':scenario.pk,'revision':scenario.revision,'gid':gid,
                'fingerprint':fingerprint(group),'powers':values,'total':proposed,'source':origin},salt=SALT,compress=True))
        except ValidationError as exc:row['error']=' '.join(exc.messages)
        rows.append(row)
    return {'rows':rows,'source':metadata}


def apply(scenario,post):
    try:tokens=json.loads(post.get('tokens','[]'))
    except (ValueError,TypeError):raise ValidationError('Некорректный список предложений прогноза.')
    if not isinstance(tokens,list) or not tokens or any(not isinstance(token,str) for token in tokens) or len(tokens)>len(scenario.state['groups']):raise ValidationError('Выберите предложения для применения.')
    state=deepcopy(scenario.state);seen=set();only_empty=post.get('only_empty')=='1'
    for token in tokens:
        try:proposal=signing.loads(token,salt=SALT,max_age=3600)
        except (signing.BadSignature,TypeError):raise ValidationError('Предпросмотр прогноза устарел. Рассчитайте его заново.')
        gid=proposal.get('gid');group=state['groups'].get(gid)
        if not group or gid in seen or proposal.get('scenario')!=scenario.pk or proposal.get('revision')!=scenario.revision or proposal.get('fingerprint')!=fingerprint(group):raise ValidationError('Сценарий или состав группы изменился. Повторите предпросмотр.')
        if proposal.get('source',{}).get('method') in ('average_daily','same_calendar_dates') and not proposal['source'].get('stock_constraint'):
            raise ValidationError('Расчёт прогноза по истории устарел: пересчитайте с учётом остатков и поступлений.')
        seen.add(gid)
        if only_empty and has_forecast(scenario,group) and not needs_stock_repair(scenario,group):raise ValidationError('У выбранной группы уже задан прогноз. Повторите предпросмотр или отключите «Только пустые».')
        group['forecast_mode']='powers';group['forecast_before_total']=proposal['total']
        for key,value in proposal['powers'].items():group['powers'][key]['forecast_before']=value
        group['forecast_history_source']=proposal['source']
        group.pop('forecast_workspace_source',None)
    from .split_forecast_plan import validate_state
    validate_state(state)
    return state,len(seen)
