"""Three previous calendar years of actual sales. Independent of plan quantities."""
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo
import logging
from django.core.exceptions import ValidationError
from django.utils import timezone
from model_purch.conns import connect_database
from .sales_planning_scope import PRODUCT_GROUP
from .sales_planning_group_service import group_key

HISTORY_SQL = '''SELECT p.PlanningGroupOZPErp, sub.Chanel,
 YEAR(s.DateInvoice), MONTH(s.DateInvoice), CASE WHEN COUNT(s.Quantity)=COUNT(*) THEN SUM(s.Quantity) ELSE NULL END,
 SUM(s.Price - s.AmountUSDIt_ITW), SUM(s.AmountExwAEUSD), p.ModelCode
FROM DataWH.dbo.Sales s
INNER JOIN DataWH.dbo.Product p ON p.ProductCode = s.ProductCode
INNER JOIN DataWH.dbo.Subdivisions sub ON sub.SubdivisionKey = s.Subdivision
WHERE s.DateInvoice >= ? AND s.DateInvoice < ? AND p.GroupERP = ?
GROUP BY p.PlanningGroupOZPErp, sub.Chanel, YEAR(s.DateInvoice), MONTH(s.DateInvoice), p.ModelCode'''


def normalized(value):
    return str(value or '').strip().casefold()


def load_history(scenario, *, cutoff=None, include_projection=True):
    if scenario.start.year < 4:
        raise ValidationError('Для этого периода нет трёх предыдущих календарных лет.')
    start, end = date(scenario.start.year-3, 1, 1), date(scenario.start.year, 1, 1)
    today = timezone.localdate(timezone=ZoneInfo('Europe/Moscow'))
    effective_end = min(end, today + timedelta(days=1))
    if cutoff is not None:effective_end=min(effective_end,cutoff+timedelta(days=1))
    if effective_end <= start:
        raise ValidationError('Исторический период ещё не наступил.')
    connection = cursor = None
    try:
        connection, cursor = connect_database('vm-dwh','DataWH')
        connection.timeout = 60
        cursor.execute(HISTORY_SQL, str(start), str(effective_end), PRODUCT_GROUP)
        rows = cursor.fetchall()
    except Exception:
        logging.getLogger(__name__).exception('Split historical sales load failed')
        raise ValidationError('Не удалось загрузить исторический факт из DataWH. Сохранённый факт и план не изменены.')
    finally:
        try:
            if cursor is not None: cursor.close()
        finally:
            if connection is not None: connection.close()
    selected_groups = {group['ref']['source_key'] for group in scenario.state['groups'].values()}
    selected_channels = {normalized(channel['name']) for channel in scenario.state['channels'].values()}
    records = {};blocks={};details=[];invalid=set()
    mappings={}
    for group in scenario.state['groups'].values():
        mapping={}
        for power_key,power in group['powers'].items():
            for model in power['models']:
                if model['kind'] not in ('indoor','outdoor'):continue
                name=normalized(model['name']);pair=(power_key,model['kind'])
                if name in mapping and mapping[name]!=pair:mapping[name]=None
                else:mapping[name]=pair
        mappings[group['ref']['source_key']]=mapping
    for raw in rows:
        if len(raw)!=8:raise ValidationError('Источник факта не вернул модели для расчёта комплектов. Обновите загрузку.')
        name, channel, year, month, qty, revenue, cost, model_name=raw
        key, channel = group_key(str(name or '')), normalized(channel)
        if key not in selected_groups or channel not in selected_channels:
            continue
        try:
            year, month = int(year), int(month)
            values = [None if value is None else Decimal(str(value)) for value in (qty,revenue,cost)]
            if year not in range(start.year, end.year) or month not in range(1,13) or any(value is not None and not value.is_finite() for value in values):
                raise ValueError
        except (ValueError, TypeError, InvalidOperation):
            raise ValidationError('Источник вернул некорректные исторические показатели. Данные не изменены.')
        identity = (key,channel,year,month)
        pair=mappings[key].get(normalized(model_name))
        details.append({'group':key,'channel':channel,'year':year,'month':month,'model':str(model_name or ''),
                        'power':pair[0] if pair else '', 'kind':pair[1] if pair else '',
                        'qty':None if values[0] is None else str(values[0])})
        if pair:
            totals=blocks.setdefault(identity,{}).setdefault(pair[0],{'indoor':Decimal(0),'outdoor':Decimal(0)})
            if values[0] is None:invalid.add(identity)
            else:totals[pair[1]]+=values[0]
        elif values[0] is None or values[0]!=0:invalid.add(identity)
        if identity not in records:
            records[identity] = values
        else:
            records[identity] = [a+b if a is not None and b is not None else None for a,b in zip(records[identity],values)]
    for identity,values in records.items():
        values[0]=None if identity in invalid else sum((min(pair['indoor'],pair['outdoor']) for pair in blocks.get(identity,{}).values()),Decimal(0))
    snapshot = {'loaded_at':timezone.now().isoformat(), 'through':str(effective_end-timedelta(days=1)),
        'first_year':start.year, 'last_year':end.year-1, 'groups':sorted(selected_groups), 'channels':sorted(selected_channels),
        'source':'DataWH.dbo.Sales', 'quantity_unit':'sets-v1',
        'quantity_basis':'Комплекты: минимум чистых продаж внутренних и наружных блоков по мощности, каналу и месяцу; затем сумма.',
        'model_rows':details,
        'rows':[{'group':key,'channel':channel,'year':year,'month':month,
                 **dict(zip(('qty','revenue','cost'),[None if value is None else str(value) for value in values]))}
                for (key,channel,year,month),values in records.items()]}
    if include_projection and scenario.stock_date.year==scenario.start.year-1 and scenario.stock_date<effective_end-timedelta(days=1):
        snapshot['projection_snapshot']=load_history(scenario,cutoff=scenario.stock_date,include_projection=False)
    return snapshot


def presentation(scenario):
    snapshot = scenario.state.get('history')
    years = list(range(max(1,scenario.start.year-3),scenario.start.year))
    data = {(row['group'],row['channel'],row['year'],row['month']):row for row in snapshot['rows']} if snapshot else {}
    through = date.fromisoformat(snapshot['through']) if snapshot else None
    legacy = bool(snapshot and snapshot.get('quantity_unit')!='sets-v1')
    result = []
    for gid, group in scenario.state['groups'].items():
        periods = []
        for year in years:
            monthly = []
            for month in range(1,13):
                cells = []
                for cid,ch in scenario.state['channels'].items():
                    key, channel = group['ref']['source_key'], normalized(ch['name'])
                    covered = snapshot and key in snapshot['groups'] and channel in snapshot['channels'] and date(year,month,1) <= through
                    row = data.get((key,channel,year,month))
                    cell = {field:Decimal(row[field]) if row and row[field] is not None else None if row or not covered else Decimal(0)
                            for field in ('qty','revenue','cost')}
                    if legacy:
                        cell['qty']=None
                    cell.update(cid=cid, channel=ch['name'], markup=cell['revenue']/cell['cost'] if cell['revenue'] is not None and cell['cost'] else None)
                    cells.append(cell)
                monthly.append({'month':f'{year}-{month:02}', 'cells':cells})
            annual = []
            for index, (cid,ch) in enumerate(scenario.state['channels'].items()):
                available = [row['cells'][index] for row in monthly if through and date(year,int(row['month'][-2:]),1) <= through]
                cell = {field:sum((row[field] for row in available),Decimal(0)) if available and all(row[field] is not None for row in available) else None for field in ('qty','revenue','cost')}
                cell.update(cid=cid, channel=ch['name'], markup=cell['revenue']/cell['cost'] if cell['revenue'] is not None and cell['cost'] else None)
                annual.append(cell)
            periods.append({'year':year,'annual':annual,'months':monthly})
        result.append({'gid':gid,'name':group['ref']['name'],'years':periods})
    return {'snapshot':snapshot,'groups':result,'years':years,'quantity_needs_reload':legacy,'quantity_note':('Факт сохранён в штуках без моделей. Для расчёта комплектов '+('создайте корректировку и загрузите факт.' if scenario.status=='approved' else 'нажмите «Загрузить факт».') if legacy else 'Количество факта — комплекты по мощностям. Несопоставленные модели или неизвестное количество не заменяются нулём.')}
