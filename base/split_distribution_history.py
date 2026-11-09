"""Read-only five-year quantity basis, cut off at a completed stock-date month."""
from calendar import monthrange
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import logging

from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.utils import timezone
from model_purch.conns import connect_database
from .sales_planning_scope import PRODUCT_GROUP
from .split_engine import allocate, HUNDRED, planning_months, precise, stored
from .split_history import normalized

SQL = '''SELECT sub.Chanel, YEAR(s.DateInvoice), MONTH(s.DateInvoice),
 p.ModelCode, SUM(s.Quantity)
FROM DataWH.dbo.Sales s
INNER JOIN DataWH.dbo.Product p ON p.ProductCode = s.ProductCode
INNER JOIN DataWH.dbo.Subdivisions sub ON sub.SubdivisionKey = s.Subdivision
WHERE s.DateInvoice >= ? AND s.DateInvoice < ? AND p.GroupERP = ?
 AND LTRIM(RTRIM(p.PlanningGroupOZPErp)) = ?
GROUP BY sub.Chanel, YEAR(s.DateInvoice), MONTH(s.DateInvoice), p.ModelCode'''


def period(scenario):
    if scenario.start.year < 6:
        raise ValidationError('Для этого периода нет пяти предыдущих календарных лет.')
    start, end = date(scenario.start.year-5, 1, 1), date(scenario.start.year, 1, 1)
    stock = scenario.stock_date
    through = stock if stock.day == monthrange(stock.year, stock.month)[1] else stock.replace(day=1)-timedelta(days=1)
    end = min(end, through+timedelta(days=1))
    if end <= start:
        raise ValidationError('До даты стока нет завершённых месяцев исторического периода.')
    return start, end


def load_basis(scenario, group, *, refresh=False):
    start, end = period(scenario)
    identity = f"v1|{start}|{end}|{scenario.stock_date}|{group['ref']['source_key']}"
    key = 'split-distribution-' + sha256(identity.encode()).hexdigest()
    cached = None if refresh else cache.get(key)
    if cached is not None:
        return cached
    connection = cursor = None
    try:
        connection, cursor = connect_database('vm-dwh', 'DataWH')
        connection.timeout = 60
        cursor.execute(SQL, str(start), str(end), PRODUCT_GROUP, group['ref']['name'])
        rows = cursor.fetchall()
    except Exception:
        logging.getLogger(__name__).exception('Split distribution source failed')
        raise ValidationError('Не удалось получить распределение из DataWH. Можно ввести доли вручную; сохранённые значения не изменены.')
    finally:
        try:
            if cursor is not None: cursor.close()
        finally:
            if connection is not None: connection.close()
    records = []
    for channel, year, month, model, qty in rows:
        try:
            year, month, qty = int(year), int(month), Decimal(str(qty))
            if not qty.is_finite() or not (start <= date(year, month, 1) < end):
                raise ValueError
        except (ValueError, TypeError, InvalidOperation):
            raise ValidationError('Источник вернул некорректные количества или периоды. Доли не изменены.')
        records.append({'channel': normalized(channel), 'year':year, 'month':month,
                        'model':normalized(model), 'qty':str(qty)})
    result = {'first_year':start.year, 'last_year':scenario.start.year-1,
              'through':str(end-timedelta(days=1)), 'stock_date':str(scenario.stock_date),
              'loaded_at':timezone.now().isoformat(), 'rows':records}
    cache.set(key, result, 600)
    return result


@precise
def proposal(scenario, group, channel_name, basis, section):
    start, end = period(scenario)
    if basis['stock_date'] != str(scenario.stock_date) or basis['through'] != str(end-timedelta(days=1)):
        raise ValidationError('Отсечение источника не совпадает с датой стока.')
    if section == 'months':
        values = {month:Decimal(0) for month in range(1,13)}
        for row in basis['rows']:
            if row['channel'] == normalized(channel_name):
                values[row['month']] += Decimal(row['qty'])
        observations = {month:sum(start <= date(year,month,1) < end for year in range(start.year,scenario.start.year))
                        for month in range(1,13)}
        keys, weights = planning_months(scenario), []
        for key in keys:
            year, month = map(int,key.split('-'))
            if not observations[month]:
                raise ValidationError('Для части плановых месяцев нет завершённых исторических месяцев. Задайте доли вручную.')
            days = monthrange(year,month)[1]
            covered = (min(scenario.end,date(year,month,days))-max(scenario.start,date(year,month,1))).days+1
            weights.append(values[month]/observations[month]*Decimal(covered)/days)
    else:
        keys = [key for key,power in group['powers'].items() if power['active']]
        model_powers = {}
        for key,power in group['powers'].items():
            for model in power['models']:
                model_powers.setdefault(normalized(model['name']),set()).add(key)
        values = {key:Decimal(0) for key in keys}
        for row in basis['rows']:
            qty = Decimal(row['qty'])
            if not qty:
                continue
            mapped = model_powers.get(row['model'],set())
            if len(mapped) != 1 or next(iter(mapped)) not in values:
                raise ValidationError('Часть исторических моделей не сопоставлена с активными мощностями. Обновите состав группы или задайте доли мощностей вручную.')
            values[next(iter(mapped))] += qty
        weights = [values[key] for key in keys]
    if not weights or any(value < 0 for value in weights) or sum(weights) <= 0:
        raise ValidationError('Нет положительной базы продаж для распределения либо возвраты дали отрицательные доли. Задайте доли вручную.')
    return dict(zip(keys,map(stored,allocate(HUNDRED,weights))))


def needs_default(group, cid, section):
    if section == 'months':
        line = group['channels'][cid]
        return line.get('allocation','percent') == 'percent' and all(value is None or value == '' for value in line['months'].values())
    active = [power for power in group['powers'].values() if power['active']]
    return bool(active) and all(power.get('share') in (None,'') for power in active)


def apply_proposal(group, cid, section, values):
    if section == 'months':
        group['channels'][cid]['allocation'] = 'percent'
        group['channels'][cid]['months'] = values
    else:
        for key,power in group['powers'].items():
            power['share'] = values.get(key,'0')


def metadata(basis):
    return {key:value for key,value in basis.items() if key != 'rows'}
