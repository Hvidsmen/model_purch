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
from .sales_planning_scope import PRODUCT_GROUP, split_goods
from .split_engine import allocate, HUNDRED, planning_months, precise, stored
from .split_history import normalized

SQL = '''SELECT sub.Chanel, YEAR(s.DateInvoice), MONTH(s.DateInvoice),
 p.ModelCode, SUM(s.Quantity)
FROM DataWH.dbo.Sales s
INNER JOIN DataWH.dbo.Product p ON p.ProductCode = s.ProductCode
INNER JOIN DataWH.dbo.Subdivisions sub ON sub.SubdivisionKey = s.Subdivision
WHERE s.DateInvoice >= ? AND s.DateInvoice < ? AND p.GroupERP = ?
 /*SCOPE*/
GROUP BY sub.Chanel, YEAR(s.DateInvoice), MONTH(s.DateInvoice), p.ModelCode'''


class NoDistributionBase(ValidationError):
    """This scope has no usable positive basis; a broader scope may be tried."""


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


def load_basis(scenario, group, *, refresh=False, scope='planning_group'):
    start, end = period(scenario)
    brands = sorted({str(value).strip() for value in group['ref'].get('brand',[]) if str(value).strip()})
    if scope == 'planning_group':
        condition, parameters = 'AND LTRIM(RTRIM(p.PlanningGroupOZPErp)) = ?', [group['ref']['name']]
        selection = group['ref']['source_key']
    elif scope == 'product_brand' and brands:
        condition = 'AND LTRIM(RTRIM(p.BrandName)) IN ('+', '.join('?' for _ in brands)+')'
        parameters, selection = brands, '|'.join(normalized(value) for value in brands)
    elif scope == 'product_group':
        condition, parameters, selection = '', [], PRODUCT_GROUP
    else:
        raise NoDistributionBase('Бренд группы не задан.')
    identity = f"v2|{start}|{end}|{scenario.stock_date}|{scope}|{selection}"
    key = 'split-distribution-' + sha256(identity.encode()).hexdigest()
    cached = None if refresh else cache.get(key)
    if cached is not None:
        return cached
    connection = cursor = None
    try:
        connection, cursor = connect_database('vm-dwh', 'DataWH')
        connection.timeout = 60
        cursor.execute(SQL.replace('/*SCOPE*/',condition), str(start), str(end), PRODUCT_GROUP, *parameters)
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
              'loaded_at':timezone.now().isoformat(), 'scope':scope,
              'product_group':PRODUCT_GROUP, 'brands':brands if scope == 'product_brand' else [], 'rows':records}
    cache.set(key, result, 600)
    return result


@precise
def proposal(scenario, group, channel_name, basis, section, *, all_channels=False):
    start, end = period(scenario)
    if basis['stock_date'] != str(scenario.stock_date) or basis['through'] != str(end-timedelta(days=1)):
        raise ValidationError('Отсечение источника не совпадает с датой стока.')
    if section == 'months':
        values = {month:Decimal(0) for month in range(1,13)}
        for row in basis['rows']:
            if all_channels or row['channel'] == normalized(channel_name):
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
        broad = basis.get('scope') in ('product_brand','product_group')
        if broad:
            # Broader groups have different models; match their reference power class,
            # never substitute their model selections into the target group.
            model_powers = {}
            for name,power in split_goods(include_inactive=True).values_list('name','power_class').iterator():
                if str(power or '').strip():
                    model_powers.setdefault(normalized(name),set()).add(str(power).strip())
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
                if broad:
                    continue
                raise NoDistributionBase('Часть исторических моделей не сопоставлена с активными мощностями. Обновите состав группы или задайте доли мощностей вручную.')
            values[next(iter(mapped))] += qty
        weights = [values[key] for key in keys]
    if any(value < 0 for value in weights):
        raise ValidationError('Возвраты дали отрицательные доли. Проверьте факт или задайте доли вручную.')
    if not weights or sum(weights) <= 0:
        raise NoDistributionBase('Нет положительной базы продаж для распределения.')
    return dict(zip(keys,map(stored,allocate(HUNDRED,weights))))


def resolve_proposal(scenario, group, channel_name, section, *, refresh=False):
    """Use the first usable scope; do not merge a narrow and a broad distribution."""
    candidates = []
    if section == 'months':
        candidates.append(('planning_group',False,'planning_group_channel','Группа планирования + канал',group['ref']['name']+' · '+channel_name))
    candidates.append(('planning_group',True,'planning_group_all_channels','Группа планирования · все каналы',group['ref']['name']))
    brands = [str(value).strip() for value in group['ref'].get('brand',[]) if str(value).strip()]
    if brands:
        candidates.append(('product_brand',True,'product_group_brand','Товарная группа + бренд',PRODUCT_GROUP+' · '+', '.join(brands)+' · все каналы'))
    candidates.append(('product_group',True,'product_group','Товарная группа',PRODUCT_GROUP+' · все каналы'))
    loaded = {}
    for scope,all_channels,rule,label,detail in candidates:
        try:
            if scope not in loaded:
                loaded[scope] = load_basis(scenario,group,refresh=refresh,scope=scope)
            basis = loaded[scope]
            values = proposal(scenario,group,channel_name,basis,section,all_channels=all_channels)
            chosen = {**basis,'rule_key':rule,'rule_label':label,'rule_detail':detail}
            if section == 'powers' and scope != 'planning_group':
                chosen['power_scope'] = 'Только сопоставленные мощности планируемой группы: '+', '.join(values)
            return values,chosen
        except NoDistributionBase:
            continue
    raise ValidationError('Не найдена база распределения по группе и каналу, всем каналам группы, товарной группе с брендом и товарной группе. Задайте доли вручную.')


def needs_default(group, cid, section):
    if section == 'months':
        line = group['channels'][cid]
        return line.get('allocation','percent') == 'percent' and all(value is None or value == '' for value in line['months'].values())
    active = [power for power in group['powers'].values() if power['active']]
    return bool(active) and all(power.get('share') in (None,'') for power in active)


def apply_proposal(group, cid, section, values):
    if section == 'months':
        group['channels'][cid]['allocation'] = 'percent'
        group['channels'][cid]['distribution_mode'] = 'fact'
        group['channels'][cid]['months'] = values
    else:
        for key,power in group['powers'].items():
            power['share'] = values.get(key,'0')


def metadata(basis):
    return {key:value for key,value in basis.items() if key != 'rows'}
