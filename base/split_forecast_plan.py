"""Pre-plan demand = remaining ERP plan * matched-period execution, by group."""
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
from .split_history import normalized
from .split_engine import ZERO,STEP,HUNDRED,allocate,number,stored,precise
from .split_ddp import fingerprint
from .split_preplan_forecast import resolve
from .split_forecast_history import SALT,source as fact_source,has_forecast

PLAN_SQL='''WITH dates AS (
 SELECT CAST(? AS date) AS elapsed_from,CAST(? AS date) AS cutoff,
 CAST(? AS date) AS remaining_from,CAST(? AS date) AS remaining_through
), calendar AS (
 SELECT d.CalendarYear,d.MonthNumberOfYear,COUNT(*) AS work_days,
 SUM(CASE WHEN d.FullDateAlternateKey BETWEEN t.elapsed_from AND t.cutoff THEN 1 ELSE 0 END) AS past_days,
 SUM(CASE WHEN d.FullDateAlternateKey BETWEEN t.remaining_from AND t.remaining_through THEN 1 ELSE 0 END) AS future_days
 FROM DataWH.dbo.Date d CROSS JOIN dates t WHERE d.IsWorkDay=1
 GROUP BY d.CalendarYear,d.MonthNumberOfYear
)
SELECT m.PlanningGroupOZPErp,n.[Наименование],
 SUM(CAST(p.[Количество] AS decimal(28,12))*c.past_days/c.work_days),
 SUM(CAST(p.[Количество] AS decimal(28,12))*c.future_days/c.work_days),
 SUM(CASE WHEN p.[Количество] IS NULL THEN 1 ELSE 0 END),MAX(CASE WHEN c.future_days>0 THEN 1 ELSE 0 END),
 MAX(CASE WHEN c.past_days>0 THEN 1 ELSE 0 END)
 FROM DataWH.erp.[РегистрыНакопления.ПланыПродаж] p
 JOIN DataWH.erp.[Справочники.Номенклатура] n ON n.[Ссылка]=p.[НоменклатураСсылка]
 JOIN (SELECT DISTINCT ModelCode,PlanningGroupOZPErp FROM DataWH.dbo.ModelCodeERP WHERE GroupERP=?) m ON m.ModelCode=n.[Наименование]
 JOIN DataWH.dbo.Subdivisions sub ON sub.SubdivisionKey=p.[Подразделение]
 JOIN calendar c ON c.CalendarYear=YEAR(p.[Период]) AND c.MonthNumberOfYear=MONTH(p.[Период])
 WHERE p.[Сценарий]=? AND p.[Период]>=? AND p.[Период]<? AND sub.Chanel IN (__CHANNELS__)
 GROUP BY m.PlanningGroupOZPErp,n.[Наименование]'''
VERSIONS_SQL='''SELECT DISTINCT CONVERT(nvarchar(255),p.[Сценарий])
 FROM DataWH.erp.[РегистрыНакопления.ПланыПродаж] p
 JOIN DataWH.erp.[Справочники.Номенклатура] n ON n.[Ссылка]=p.[НоменклатураСсылка]
 JOIN (SELECT DISTINCT ModelCode FROM DataWH.dbo.ModelCodeERP WHERE GroupERP=?) m ON m.ModelCode=n.[Наименование]
 JOIN DataWH.dbo.Subdivisions sub ON sub.SubdivisionKey=p.[Подразделение]
 WHERE p.[Сценарий] IS NOT NULL AND p.[Период]>=? AND p.[Период]<? AND sub.Chanel IN (__CHANNELS__) ORDER BY 1'''


def shown(value):
    text=stored(value)
    return text.rstrip('0').rstrip('.') if '.' in text else text


def channels(scenario):
    result=sorted({ch['name'] for ch in scenario.state['channels'].values()})
    if not result:raise ValidationError('Сначала добавьте каналы продаж.')
    return result


def query(sql,params):
    connection=cursor=None
    try:
        connection,cursor=connect_database('vm-dwh','DataWH');connection.timeout=120
        cursor.execute(sql,*params);return cursor.fetchall()
    except Exception:
        logging.getLogger(__name__).exception('ERP plan execution source failed')
        raise ValidationError('Не удалось прочитать регистр планов продаж. Прогноз не изменён.')
    finally:
        try:
            if cursor is not None:cursor.close()
        finally:
            if connection is not None:connection.close()


def versions(scenario):
    selected=channels(scenario)
    rows=query(VERSIONS_SQL.replace('__CHANNELS__',','.join('?' for _ in selected)),[PRODUCT_GROUP,date(scenario.stock_date.year,1,1),scenario.start,*selected])
    return [str(row[0]) for row in rows]


def plan_source(scenario,version,elapsed_from):
    selected=channels(scenario);first=scenario.stock_date+timedelta(days=1);last=scenario.start-timedelta(days=1)
    after_last=last.replace(day=1)+timedelta(days=32);after_last=after_last.replace(day=1)
    params=[elapsed_from,scenario.stock_date,first,last,PRODUCT_GROUP,version,elapsed_from.replace(day=1),after_last,*selected]
    rows=query(PLAN_SQL.replace('__CHANNELS__',','.join('?' for _ in selected)),params);result={}
    for record in rows:
        name,model,past,future,unknown,future_seen=record[:6]
        past_seen=bool(record[6]) if len(record)>6 else past is not None and Decimal(str(past))!=ZERO
        entry=result.setdefault((group_key(str(name or '')),normalized(model)),{'past':ZERO,'future':ZERO,'unknown':False,'future_seen':False,'past_seen':False})
        try:values=[None if value is None else Decimal(str(value)) for value in (past,future)]
        except (ValueError,ArithmeticError):raise ValidationError('В регистре плана некорректное количество.')
        if any(value is not None and not value.is_finite() for value in values):raise ValidationError('В регистре плана некорректное количество.')
        entry['unknown'] |= bool(unknown) or any(value is None for value in values)
        entry['future_seen'] |= bool(future_seen)
        entry['past_seen'] |= past_seen
        for field,value in zip(('past','future'),values):
            if value is not None:entry[field]+=value
    return result


@precise
def grouped_amounts(group,own_plan,own_fact,mapping):
    """Prefer model detail; split recognized group-level PG rows only if detail is absent."""
    totals={key:{kind:{'past':ZERO,'future':ZERO,'fact':ZERO} for kind in ('indoor','outdoor')} for key in group['powers']}
    grouped={field:ZERO for field in ('past','future','fact')}
    group_seen={field:False for field in grouped};detail_seen={field:False for field in grouped}
    for records,fields in [(own_plan,('past','future')),(own_fact,('qty',))]:
        for model,entry in records.items():
            if entry['unknown']:raise ValidationError('Источник содержит неизвестное количество.')
            aggregate=model.startswith('pg_') and group_key(model[3:])==group['ref']['source_key']
            if model not in mapping and not aggregate:
                if any(entry[field]!=ZERO for field in fields):raise ValidationError('Нет сопоставления модели с мощностью: '+model)
                continue
            for field in fields:
                target='fact' if field=='qty' else field
                seen=(entry.get(field+'_seen',entry[field]!=ZERO) if field in ('past','future') else True)
                if aggregate and model not in mapping:
                    grouped[target]+=entry[field];group_seen[target]|=seen
                else:
                    key,kind=mapping[model];totals[key][kind][target]+=entry[field];detail_seen[target]|=seen
    by_power={key:{field:min(parts[kind][field] for kind in ('indoor','outdoor')) for field in grouped} for key,parts in totals.items()}
    fallback=[field for field in grouped if group_seen[field] and not detail_seen[field]]
    if fallback:
        keys=list(group['powers'])
        shares=[number(power.get('share'),optional=True) if power.get('active') else ZERO for power in group['powers'].values()]
        if any(value is None or value>HUNDRED for value in shares) or sum(shares,ZERO)!=HUNDRED:
            raise ValidationError('В ERP только общий объём группы: заполните доли активных мощностей на шаге 3, сумма должна быть 100%.')
        for field in fallback:
            if grouped[field]<ZERO:raise ValidationError('Общий план или факт группы отрицателен.')
            for key,value in zip(keys,allocate(grouped[field],shares) if grouped[field] else [ZERO]*len(keys)):
                by_power[key][field]=value
    return totals,by_power,fallback


def available_before(power,key):
    totals=[]
    for kind in ('indoor','outdoor'):
        amounts=[]
        for field in ('stock','prior'):
            raw=power.get(field+'_'+kind)
            if raw is None or str(raw).strip()=='':raise ValidationError(key+': загрузите остатки и поступления до начала плана для обоих блоков.')
            try:value=Decimal(str(raw).replace(',','.'))
            except (ValueError,ArithmeticError):raise ValidationError(key+': некорректные остатки или поступления.')
            number(abs(value));amounts.append(value)
        total=sum(amounts,ZERO)
        if total<ZERO:raise ValidationError(key+': исходный остаток с поступлениями отрицателен; исправьте данные источника.')
        totals.append(total)
    return min(totals)


def validate_state(state):
    for group in state.get('groups',{}).values():
        source=group.get('forecast_history_source') or {}
        if not (source.get('method')=='plan_execution' or source.get('stock_constraint')=='before-start-v1') or group.get('forecast_workspace_source') or group.get('forecast_mode')!='powers':continue
        for key,power in group['powers'].items():
            value=number(power.get('forecast_before'),optional=True)
            if value is not None and value>available_before(power,key):
                raise ValidationError(group['ref']['name']+' · '+key+': прогноз превышает остатки и поступления до начала. Уменьшите прогноз или пересчитайте его с актуальными исходными данными.')


@precise
def preview(scenario,post):
    try:targets=json.loads(post.get('targets','[]'));elapsed_from=date.fromisoformat(post.get('elapsed_from',''))
    except (ValueError,TypeError):raise ValidationError('Выберите группы и начало периода выполнения.')
    if not isinstance(targets,list) or not targets or any(not isinstance(gid,str) or gid not in scenario.state['groups'] for gid in targets) or len(set(targets))!=len(targets):raise ValidationError('Выберите группы текущего сценария.')
    version=post.get('plan_version','').strip()
    if not version or len(version)>255:raise ValidationError('Выберите версию из регистра планов продаж.')
    cutoff=scenario.stock_date;first=cutoff+timedelta(days=1);last=scenario.start-timedelta(days=1)
    if elapsed_from.year<2000 or elapsed_from>cutoff:raise ValidationError('Начало периода выполнения должно быть не позднее даты среза.')
    if first>last:raise ValidationError('Между срезом и началом плана нет периода прогноза.')
    if cutoff>timezone.localdate(timezone=ZoneInfo('Europe/Moscow')):raise ValidationError('Дата среза ещё не наступила: факт выполнения недоступен.')
    plans=plan_source(scenario,version,elapsed_from);facts,selected_channels=fact_source(scenario,elapsed_from,cutoff)
    metadata={'source':'Регистр планов продаж · '+version,'method':'plan_execution','plan_version':version,
        'from':str(elapsed_from),'through':str(cutoff),'target_from':str(first),'target_through':str(last),
        'channels':selected_channels,'loaded_at':timezone.now().isoformat(),
        'calculation':'Факт / план за одинаковый прошедший период × план на оставшийся период. Неполные месяцы плана — по рабочим дням.'}
    rows=[]
    for gid in targets:
        group=scenario.state['groups'][gid];current=resolve(scenario,group)['total']
        row={'gid':gid,'name':group['ref']['name'],'current':None if current is None else stored(current),'has_current':has_forecast(scenario,group),'proposed':None,'powers':[],'error':''}
        try:
            mapping={}
            for key,power in group['powers'].items():
                for model in power['models']:
                    if model['kind'] not in ('indoor','outdoor'):continue
                    name=normalized(model['name']);pair=(key,model['kind'])
                    if name in mapping and mapping[name]!=pair:raise ValidationError('Модель неоднозначно сопоставлена с мощностью: '+model['name'])
                    mapping[name]=pair
            own_plan={model:entry for (g,model),entry in plans.items() if g==group['ref']['source_key']}
            own_fact={model:entry for (g,model),entry in facts.items() if g==group['ref']['source_key']}
            if not own_plan or not any(v['future_seen'] for v in own_plan.values()):raise ValidationError('Нет плана этой группы на оставшийся период в выбранной версии.')
            if not own_fact:raise ValidationError('Нет факта этой группы за период выполнения; отсутствие данных не заменяется нулём.')
            totals,by_power,fallback=grouped_amounts(group,own_plan,own_fact,mapping)
            if any(v<0 for values in by_power.values() for v in values.values()):raise ValidationError('Отрицательный чистый факт или план: проверьте возвраты и регистр.')
            past=sum((v['past'] for v in by_power.values()),ZERO);actual=sum((v['fact'] for v in by_power.values()),ZERO)
            remaining=sum((v['future'] for v in by_power.values()),ZERO)
            if past<=ZERO:raise ValidationError('План прошедшего периода равен нулю или отсутствует: процент выполнения не определён.')
            ratio=actual/past
            requested={key:(v['future']*ratio).quantize(STEP,rounding=ROUND_HALF_UP) for key,v in by_power.items()}
            available={key:available_before(group['powers'][key],key) for key in by_power}
            values={key:stored(number(min(requested[key],available[key]))) for key in by_power}
            unlimited=sum(requested.values(),ZERO)
            limited=any(requested[key]>available[key] for key in by_power)
            pct=stored((ratio*100).quantize(STEP,rounding=ROUND_HALF_UP));proposed=stored(number(sum((Decimal(v) for v in values.values()),ZERO)))
            origin={**metadata,'execution_percent':pct,'past_plan':stored(past),'past_fact':stored(actual),'remaining_plan':stored(remaining),'rule':'remaining-erp-plan-times-group-execution-stock-cap-v1','stock_cap_applied':limited,'unconstrained_forecast':stored(unlimited),'available_before':{key:stored(value) for key,value in available.items()},'power_share_fallback':fallback,'power_shares':{key:power.get('share') for key,power in group['powers'].items()} if fallback else {}}
            fallback_note=(' Общий объём PG распределён по сохранённым долям мощностей шага 3 ('+', '.join({'past':'прошедший план','future':'оставшийся план','fact':'факт'}[field] for field in fallback)+').' if fallback else '')
            row.update(proposed=proposed,explanation='Выполнение '+shown(Decimal(pct))+'%: факт '+shown(actual)+' / план '+shown(past)+'; оставшийся план '+shown(remaining)+' компл. Прогноз по выполнению '+shown(unlimited)+'; обеспечено товаром '+shown(Decimal(proposed))+'.'+(' Ограничено остатками и поступлениями до начала плана.' if limited else '')+fallback_note,
                powers=[{'power':key,'sets':values[key],'requested':stored(requested[key]),'available':stored(available[key]),'indoor':stored(totals[key]['indoor']['fact']),'outdoor':stored(totals[key]['outdoor']['fact'])} for key in values],
                token=signing.dumps({'scenario':scenario.pk,'revision':scenario.revision,'gid':gid,'fingerprint':fingerprint(group),'powers':values,'total':proposed,'source':origin},salt=SALT,compress=True))
        except (ValidationError,ArithmeticError) as exc:row['error']=' '.join(exc.messages) if isinstance(exc,ValidationError) else 'Расчёт вышел за допустимую точность. Проверьте базу выполнения.'
        rows.append(row)
    return {'rows':rows,'source':metadata}
