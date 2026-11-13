"""Per-coefficient participation rules shared by Python and generated T-SQL."""
from contextvars import ContextVar
from contextlib import contextmanager
from decimal import Decimal
import re

KINDS=('Политики','Продажи','Директора')
DEFAULT_RULES=(('group','Политики','HEAT'),('channel','Политики','ДПП'))
_current=ContextVar('motivation_dimension_exclusions',default=None)
DIMENSION_MARKER='__MOTIVATION_DIMENSION_SCOPE__'


def kind_name(value):
    normalized=str(value or '').strip().casefold()
    return {'политика':'Политики','политики':'Политики','продажи':'Продажи','директор':'Директора','директора':'Директора'}.get(normalized,str(value or '').strip())


def normalize(value,dimension):
    value=str(value or '').strip().upper()
    return re.sub(r'^[0-9. ]+','',value) if dimension=='group' else value


def rules():return DEFAULT_RULES if _current.get() is None else _current.get()


@contextmanager
def rule_scope():
    if _current.get() is not None:
        yield rules();return
    from ..models import MotivationPlanningGroupExclusion,MotivationChannelExclusion
    rows=[]
    for dimension,model in (('group',MotivationPlanningGroupExclusion),('channel',MotivationChannelExclusion)):
        rows.extend((dimension,kind_name(kind),normalize(value,dimension)) for kind,value in
                    model.objects.filter(enabled=True).values_list('coefficient_type','value'))
    token=_current.set(tuple(sorted(set(rows))))
    try:yield rules()
    finally:_current.reset(token)


def allowed(kind,planning_group,channel=''):
    kind=kind_name(kind)
    values={'group':normalize(planning_group,'group'),'channel':normalize(channel,'channel')}
    return not any(k==kind and values[dimension]==value for dimension,k,value in rules())


def group_sql(expression):
    value=f"UPPER(LTRIM(RTRIM(COALESCE({expression},N''))))"
    return f"COALESCE(SUBSTRING({value},NULLIF(PATINDEX(N'%[^0-9. ]%',{value}),0),255),N'')"


def sql_allowed(kind=None,*,type_expression='[ТипКоэф]',group_expression='[ГруппаПлановПродаж]',channel_expression='[Канал]'):
    clauses=[]
    for dimension,rule_kind,value in sorted(set(rules())):
        if kind is not None and kind_name(kind)!=rule_kind:continue
        expression=group_sql(group_expression) if dimension=='group' else f"UPPER(LTRIM(RTRIM(COALESCE({channel_expression},N''))))"
        match=expression+"=N'"+value.replace("'","''")+"'"
        if kind is None:
            literal_kind=rule_kind.replace("'","''")
            match=f"LTRIM(RTRIM({type_expression}))=N'{literal_kind}' AND "+match
        clauses.append('NOT ('+match+')')
    return ' AND '.join(clauses) or '1=1'


def channels_changed(kind=None):
    selected=lambda rows:{(k,v) for dimension,k,v in rows if dimension=='channel' and (kind is None or k==kind_name(kind))}
    return selected(rules())!=selected(DEFAULT_RULES)


def channels_known(segments,sources=None):
    if segments.get('channels_complete') is False:return False
    return bool(sources and all('channel' in part for part in sources.values())) or 'channels' in segments


def channel_bases(segments,sources=None):
    if sources and all('channel' in part for part in sources.values()):
        result={}
        for part in sources.values():
            target=result.setdefault(str(part['channel']),[Decimal(0)]*5)
            for i in range(5):target[i]+=Decimal(str(part['segments'].get(f'O{i}',0)))
        return result
    if segments.get('channels') is not None:
        return {channel:[Decimal(str(v)) for v in values] for channel,values in segments['channels'].items()}
    return {'':[Decimal(str(segments.get(f'O{i}',0))) for i in range(5)]}


def amounts_for(kind,segments,planning_group,sources=None):
    if channels_changed(kind) and not channels_known(segments,sources):
        from django.core.exceptions import ValidationError
        raise ValidationError('В загруженном плане нет исходной разбивки по каналам. Один раз перезагрузите план из MS SQL для применения изменённых исключений каналов.')
    result=[Decimal(0)]*5
    for channel,values in channel_bases(segments,sources).items():
        if allowed(kind,planning_group,channel):result=[a+b for a,b in zip(result,values)]
    return result


def merge_channels(target,source):
    for channel,values in source.items():
        target[channel]=[a+b for a,b in zip(target.get(channel,[Decimal(0)]*5),values)]
    return target


def serial_channels(values):return {channel:list(map(str,amounts)) for channel,amounts in values.items()}
