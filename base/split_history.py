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
 YEAR(s.DateInvoice), MONTH(s.DateInvoice), SUM(s.Quantity),
 SUM(s.Price - s.AmountUSDIt_ITW), SUM(s.AmountExwAEUSD)
FROM DataWH.dbo.Sales s
INNER JOIN DataWH.dbo.Product p ON p.ProductCode = s.ProductCode
INNER JOIN DataWH.dbo.Subdivisions sub ON sub.SubdivisionKey = s.Subdivision
WHERE s.DateInvoice >= ? AND s.DateInvoice < ? AND p.GroupERP = ?
GROUP BY p.PlanningGroupOZPErp, sub.Chanel, YEAR(s.DateInvoice), MONTH(s.DateInvoice)'''


def normalized(value):
    return str(value or '').strip().casefold()


def load_history(scenario):
    if scenario.start.year < 4:
        raise ValidationError('Для этого периода нет трёх предыдущих календарных лет.')
    start, end = date(scenario.start.year-3, 1, 1), date(scenario.start.year, 1, 1)
    today = timezone.localdate(timezone=ZoneInfo('Europe/Moscow'))
    effective_end = min(end, today + timedelta(days=1))
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
    records = {}
    for name, channel, year, month, qty, revenue, cost in rows:
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
        if identity not in records:
            records[identity] = values
        else:
            records[identity] = [a+b if a is not None and b is not None else None for a,b in zip(records[identity],values)]
    return {'loaded_at':timezone.now().isoformat(), 'through':str(effective_end-timedelta(days=1)),
        'first_year':start.year, 'last_year':end.year-1, 'groups':sorted(selected_groups), 'channels':sorted(selected_channels),
        'source':'DataWH.dbo.Sales', 'quantity_basis':'Quantity, штуки исходного источника (не пересчёт в комплекты)',
        'rows':[{'group':key,'channel':channel,'year':year,'month':month,
                 **dict(zip(('qty','revenue','cost'),[None if value is None else str(value) for value in values]))}
                for (key,channel,year,month),values in records.items()]}


def presentation(scenario):
    snapshot = scenario.state.get('history')
    years = list(range(max(1,scenario.start.year-3),scenario.start.year))
    data = {(row['group'],row['channel'],row['year'],row['month']):row for row in snapshot['rows']} if snapshot else {}
    through = date.fromisoformat(snapshot['through']) if snapshot else None
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
    return {'snapshot':snapshot,'groups':result,'years':years}
