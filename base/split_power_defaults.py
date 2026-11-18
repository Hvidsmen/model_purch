"""Complete blank power parameters from target-group fact and approved model pairs."""
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import logging
from django.core.cache import cache
from django.core.exceptions import ValidationError
from .models import SalesPlanningBlockPair
from .sales_planning_scope import PRODUCT_GROUP, split_goods
from .sales_planning_group_service import group_key, block_kind, pair_error
from .split_distribution_history import period
from .split_history import normalized
from .split_engine import number, precise, stored
from model_purch.conns import connect_database


SQL = '''SELECT p.ModelCode, SUM(s.Quantity), SUM(s.AmountExwAEUSD),
 SUM(CASE WHEN s.Quantity IS NULL OR s.AmountExwAEUSD IS NULL THEN 1 ELSE 0 END)
FROM DataWH.dbo.Sales s
INNER JOIN DataWH.dbo.Product p ON p.ProductCode = s.ProductCode
WHERE s.DateInvoice >= ? AND s.DateInvoice < ? AND p.GroupERP = ?
 AND LTRIM(RTRIM(p.PlanningGroupOZPErp)) = ?
GROUP BY p.ModelCode'''


def load_costs(scenario, group):
    start, end = period(scenario)
    key = 'split-power-costs-' + sha256(f"v1|{start}|{end}|{group['ref']['source_key']}".encode()).hexdigest()
    cached = cache.get(key)
    if cached is not None:
        return cached
    connection = cursor = None
    try:
        connection, cursor = connect_database('vm-dwh', 'DataWH')
        connection.timeout = 60
        cursor.execute(SQL, str(start), str(end), PRODUCT_GROUP, group['ref']['name'])
        source = cursor.fetchall()
    except Exception:
        logging.getLogger(__name__).exception('Split power cost source failed')
        raise ValidationError('Не удалось получить фактическую себестоимость. Доли доступны; стоимость можно заполнить вручную.')
    finally:
        try:
            if cursor is not None: cursor.close()
        finally:
            if connection is not None: connection.close()
    result = {}
    for name, qty, cost, unknown in source:
        try:
            values = [None if value is None else Decimal(str(value)) for value in (qty, cost)]
            if any(value is not None and not value.is_finite() for value in values):
                raise ValueError
        except (InvalidOperation, ValueError, TypeError):
            raise ValidationError('Факт содержит некорректные количества или себестоимость. Стоимость не заполнена.')
        identity = normalized(name)
        if not identity:
            continue
        entry = result.setdefault(identity, {'qty': Decimal(0), 'cost': Decimal(0), 'unknown': False})
        entry['unknown'] = entry['unknown'] or bool(unknown) or any(value is None for value in values)
        for field, value in zip(('qty', 'cost'), values):
            if value is not None:
                entry[field] += value
    cache.set(key, result, 600)
    return result


@precise
def defaults(scenario, group, basis):
    issues = []
    try:
        # Actual resolve_proposal includes rows. Metadata-only saved bases cannot
        # establish a fresh cost proposal and must not trigger an implicit read.
        costs = load_costs(scenario, group) if 'rows' in basis else {}
    except ValidationError as exc:
        costs = {}
        issues.extend(exc.messages)
    ids = [model['id'] for power in group['powers'].values() for model in power['models'] if str(model['id']).isdecimal()]
    live = {str(good.pk): good for good in split_goods().filter(pk__in=ids)}
    pairs = {pair.power: pair for pair in SalesPlanningBlockPair.objects.filter(group_id=group['ref']['id'])
             .select_related('group', 'indoor', 'outdoor') if not pair_error(pair)}
    parameters, kit_costs = {}, {}
    for key, power in group['powers'].items():
        if not power['active']:
            continue
        params = {'factor': power.get('factor'), 'cost': power.get('indoor_cost_percent'),
                  'indoor': power.get('indoor'), 'outdoor': power.get('outdoor')}
        selected = {}
        for kind in ('indoor', 'outdoor'):
            candidates = [model for model in power['models'] if model['kind'] == kind and model['id'] in live
                          and group_key(live[model['id']].planning_group) == group['ref']['source_key']
                          and live[model['id']].power_class.strip() == key and block_kind(live[model['id']].block_type) == kind]
            if not params[kind]:
                pair = pairs.get(key)
                saved_id = str(getattr(pair, kind + '_id', ''))
                if any(model['id'] == saved_id for model in candidates):
                    params[kind] = saved_id
                elif len(candidates) == 1:
                    params[kind] = candidates[0]['id']
                else:
                    weights = [(model['id'], costs.get(normalized(model['name']), {}).get('qty', Decimal(0))) for model in candidates]
                    maximum = max((value for _, value in weights), default=Decimal(0))
                    winners = [model_id for model_id, value in weights if value == maximum and value > 0]
                    if len(winners) == 1:
                        params[kind] = winners[0]
            selected[kind] = next((model for model in candidates if model['id'] == params[kind]), None)
        unit_costs = {}
        for kind, model in selected.items():
            entry = costs.get(normalized(model['name'])) if model else None
            if entry and not entry['unknown'] and entry['qty'] > 0 and entry['cost'] > 0:
                unit_costs[kind] = entry['cost'] / entry['qty']
        if len(unit_costs) == 2:
            kit_costs[key] = unit_costs['indoor'] + unit_costs['outdoor']
            if params['cost'] in (None, ''):
                params['cost'] = stored((unit_costs['indoor'] * 100 / kit_costs[key]).quantize(Decimal('.000001')))
        if any(not selected[kind] for kind in ('indoor', 'outdoor')):
            issues.append(key + ': нет однозначной активной пары в снимке группы.')
        if len(unit_costs) != 2 and (params['cost'] in (None, '') or params['factor'] in (None, '')):
            issues.append(key + ': нет положительной фактической себестоимости обоих выбранных блоков.')
        parameters[key] = params
    base = min(kit_costs.values(), default=None)
    if base:
        for key, value in kit_costs.items():
            if parameters[key]['factor'] in (None, ''):
                factor = (value / base).quantize(Decimal('.000001'))
                try:
                    number(factor)
                    parameters[key]['factor'] = stored(factor)
                except ValidationError:
                    issues.append(key + ': соотношение DDP выходит за допустимую точность.')
    return parameters, list(dict.fromkeys(issues))
