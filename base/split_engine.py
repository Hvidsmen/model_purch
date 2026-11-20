"""Decimal-only Split calculations. No source connections and no implicit forecasts."""
from calendar import monthrange
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation, ROUND_DOWN, ROUND_HALF_UP, ROUND_CEILING, localcontext
from functools import wraps

from django.core.exceptions import ValidationError

ZERO, HUNDRED, STEP = Decimal(0), Decimal(100), Decimal('.000001')


def precise(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        with localcontext() as context:
            context.prec = 64
            return function(*args, **kwargs)
    return wrapped


def number(value, *, optional=False, maximum=Decimal('999999999999')):
    if value is None or str(value).strip() == '':
        if optional:
            return None
        raise ValidationError('Заполните числовое значение.')
    try:
        result = Decimal(str(value).strip().replace(',', '.'))
    except InvalidOperation:
        raise ValidationError('Введите число.')
    if not result.is_finite() or result < 0 or result > maximum or result.as_tuple().exponent < -6:
        raise ValidationError('Число должно быть неотрицательным, до 12 цифр целой части и 6 знаков после запятой.')
    return result


def stored(value):
    return None if value is None else format(value, 'f')


@precise
def allocate(total, weights):
    """Six decimal places, exact total, no remainder assigned to a zero weight."""
    total = total.quantize(STEP, rounding=ROUND_HALF_UP)
    denominator = sum(weights, ZERO)
    if denominator <= 0:
        raise ValidationError('Нет положительных долей для распределения.')
    result = [ZERO] * len(weights)
    positive = [index for index, weight in enumerate(weights) if weight > 0]
    used = ZERO
    for index in positive[:-1]:
        result[index] = (total * weights[index] / denominator).quantize(STEP, rounding=ROUND_DOWN)
        used += result[index]
    result[positive[-1]] = total - used
    return result


def months_between(start, end):
    cursor = start.replace(day=1)
    result = []
    while cursor <= end:
        result.append(cursor.strftime('%Y-%m'))
        if cursor.year == end.year and cursor.month == end.month:
            break
        cursor = date(cursor.year + (cursor.month == 12), cursor.month % 12 + 1, 1)
    return result


def planning_months(scenario):
    return months_between(scenario.start, scenario.end)


def forecast_months(scenario):
    return months_between(scenario.stock_date + timedelta(days=1), scenario.start - timedelta(days=1)) if scenario.stock_date < scenario.start - timedelta(days=1) else []


def quantity(scenario, line):
    value = number(line['input'])
    if scenario.state.get('volume_mode', scenario.entry_mode) == 'annual':
        actual = number(line.get('fact'), optional=True)
        if actual is None and value == 0:
            return ZERO
        if actual is None:
            raise ValidationError('Для годовой цели заполните состоявшийся факт до начала плана.')
        if value < actual:
            raise ValidationError('Годовая цель меньше состоявшегося факта.')
        value -= actual
    return value


@precise
def target_stock(group, planned):
    percent=number(group.get('target_stock_percent'),optional=True,maximum=Decimal(1000))
    return None if planned is None or percent is None else (planned*percent/HUNDRED).quantize(STEP,rounding=ROUND_CEILING)


@precise
def evaluate(scenario):
    state, errors, warnings, lines, shipments, stock = scenario.state, [], [], [], [], []
    totals = {'quantity': ZERO, 'cost': ZERO, 'revenue': ZERO, 'closing': ZERO if state['groups'] else None}
    for gid, group in state['groups'].items():
        shipment_start = len(shipments)
        name, powers = group['ref']['name'], group['powers']
        pkeys = list(powers)
        shares = [number(powers[key].get('share'), optional=True) for key in pkeys]
        valid_shares = bool(shares) and all(value is not None for value in shares) and sum(shares, ZERO) == HUNDRED
        group_quantity = ZERO
        quantities_known = True
        for cid, line in group['channels'].items():
            prefix = name + ' · ' + state['channels'][cid]['name']
            try:
                qty = quantity(scenario, line)
            except ValidationError as exc:
                errors.append(prefix + ': ' + ' '.join(exc.messages))
                quantities_known = False
                qty = ZERO
            group_quantity += qty
            ddp, markup = number(group.get('ddp'), optional=True), number(line.get('markup'), optional=True)
            cost = qty * ddp if ddp is not None else None
            revenue = cost * markup if cost is not None and markup is not None else None
            lines.append({'gid': gid, 'cid': cid, 'group': name, 'channel': state['channels'][cid]['name'],
                          'quantity': qty, 'cost': cost, 'revenue': revenue, 'input': line})
            totals['quantity'] += qty
            for field, amount in [('cost', cost), ('revenue', revenue)]:
                if qty and amount is None:
                    totals[field] = None
                elif totals[field] is not None and amount is not None:
                    totals[field] += amount
            if not qty:
                continue
            before = len(errors)
            if not group['ref']['active']:
                errors.append(prefix + ': группа больше не входит в активный справочник.')
            if ddp is None or ddp <= 0 or markup is None or markup <= 0:
                errors.append(prefix + ': заполните положительные DDP и наценку.')
            if not valid_shares:
                errors.append(prefix + ': доли мощностей должны составлять 100%.')
            months = planning_months(scenario)
            by_quantity = line.get('allocation') == 'quantity'
            weights = [number(line.get('month_quantities', {}).get(month) if by_quantity else line['months'].get(month), optional=True) for month in months]
            target = qty if by_quantity else HUNDRED
            if any(weight is None for weight in weights) or sum((weight or ZERO for weight in weights), ZERO) != target:
                errors.append(prefix + (': месячные количества должны совпадать с объёмом группы и канала.' if by_quantity else ': заполните сезонность, сумма долей месяцев — 100%.'))
            for key in pkeys:
                power = powers[key]
                share = number(power.get('share'), optional=True)
                factor = number(power.get('factor'), optional=True)
                if not share:
                    continue
                models = {str(model['id']): model for model in power['models']}
                if not power['active'] or any(str(power.get(kind)) not in models or models[str(power.get(kind))]['kind'] != kind for kind in ('indoor', 'outdoor')):
                    errors.append(prefix + ' · ' + key + ': выберите действующую пару блоков.')
                if factor is None or factor <= 0 or number(power.get('indoor_cost_percent'), optional=True) is None:
                    errors.append(prefix + ' · ' + key + ': задайте соотношение DDP мощностей и долю стоимости внутреннего блока.')
            if len(errors) != before:
                continue
            monthly_quantity = allocate(qty, weights)
            monthly_cost, monthly_revenue = allocate(cost, monthly_quantity), allocate(revenue, monthly_quantity)
            for month, month_qty, month_cost, month_revenue in zip(months, monthly_quantity, monthly_cost, monthly_revenue):
                if month_qty == 0:
                    continue
                quantities = allocate(month_qty, shares)
                factors = [value * (number(powers[key].get('factor'), optional=True) or ZERO) for key, value in zip(pkeys, quantities)]
                costs, revenues = allocate(month_cost, factors), allocate(month_revenue, factors)
                for key, power_qty, power_cost, power_revenue in zip(pkeys, quantities, costs, revenues):
                    if not powers[key].get('share') or number(powers[key]['share']) == 0:
                        continue
                    power = powers[key]
                    pct = number(power['indoor_cost_percent'])
                    parts = [pct, HUNDRED - pct]
                    cost_parts, revenue_parts = allocate(power_cost, parts), allocate(power_revenue, parts)
                    models = {str(model['id']): model for model in power['models']}
                    for kind, component_cost, component_revenue in zip(('indoor', 'outdoor'), cost_parts, revenue_parts):
                        model = models[str(power[kind])]
                        shipments.append({'group': name, 'group_key': group['ref']['source_key'], 'power': key,
                                          'channel': state['channels'][cid]['name'], 'channel_id': cid, 'month': month,
                                          'model': model['name'], 'model_key': model['key'], 'kind': kind,
                                          'quantity': power_qty, 'cost': component_cost, 'revenue': component_revenue})
        if not group_quantity and all(power.get('stock_indoor') is None and power.get('stock_outdoor') is None for power in powers.values()):
            totals['closing'] = None
            continue
        direct_inventory=quantities_known and group_quantity==ZERO and number(group.get('buy','0'),optional=True)==ZERO
        if not valid_shares and not direct_inventory:
            totals['closing'] = None
            continue
        from .split_preplan_forecast import resolve as resolve_forecast
        resolved_forecast=resolve_forecast(scenario,group)
        if resolved_forecast['issues']:
            errors.extend(name+': '+message for message in resolved_forecast['issues'])
            totals['closing']=None
            continue
        sales_by_power = {key: ZERO for key in pkeys}
        for row in shipments[shipment_start:]:
            if row['kind'] == 'indoor':
                sales_by_power[row['power']] += row['quantity']
        if sum(sales_by_power.values(), ZERO) != group_quantity:
            totals['closing'] = None
            continue
        sales = [sales_by_power[key] for key in pkeys]
        forecasts = [resolved_forecast['powers'][key] for key in pkeys]
        buys = allocate(number(group.get('buy', '0')), shares) if valid_shares else [ZERO]*len(pkeys)
        from .split_supplies import corrections, sql_receipts, validate_corrections
        try:
            validate_corrections(group)
            supply_adjustments = corrections(group)
        except ValidationError as exc:
            errors.extend(name + ': ' + message for message in exc.messages)
            totals['closing'] = None
            continue
        try:
            reserve=target_stock(group,group_quantity)
            reserve_by_power=dict(zip(pkeys,allocate(reserve,shares))) if reserve and valid_shares else dict.fromkeys(pkeys,ZERO)
        except ValidationError as exc:
            errors.extend(name+': '+message for message in exc.messages);totals['closing']=None;continue
        for key, sold, predicted, bought in zip(pkeys, sales, forecasts, buys):
            power = powers[key]
            if not number(power.get('share'), optional=True) and group.get('forecast_mode')!='powers' and not direct_inventory:
                continue
            balances = []
            for kind in ('indoor', 'outdoor'):
                initial = number(power.get('stock_' + kind), optional=True)
                incoming = sql_receipts(power, kind)
                prior = number(power.get('prior_' + kind), optional=True) if forecast_months(scenario) else ZERO
                if any(value is None for value in (initial, incoming, prior)):
                    errors.append(name + ' · ' + key + ': заполните остатки и поступления обоих блоков (ноль указывается явно).')
                    totals['closing'] = None
                    balances = []
                    break
                balances.append(initial + prior - predicted + incoming + supply_adjustments[key] + bought - sold)
            if not balances:
                continue
            closing = min(balances)
            if totals['closing'] is not None:
                totals['closing'] += closing
            stock.append({'group': name, 'power': key, 'quantity': sold, 'forecast': predicted, 'buy': bought,
                          'supply_adjustment': supply_adjustments[key], 'incoming_sql_indoor': sql_receipts(power, 'indoor'),
                          'incoming_sql_outdoor': sql_receipts(power, 'outdoor'),
                          'indoor': balances[0], 'outdoor': balances[1], 'closing': closing})
            if closing < 0:
                warnings.append(name + ' · ' + key + ': дефицит на конец периода ' + str(closing) + ' комплектов.')
            if reserve is not None and ZERO<=closing<reserve_by_power[key]:
                warnings.append(name+' · '+key+': остаток ниже целевого запаса: '+str(closing)+' из '+str(reserve_by_power[key])+' комплектов.')
    if not totals['quantity']:
        errors.append('План пуст: задайте положительный объём хотя бы для одной группы и канала.')
    return {'lines': lines, 'shipments': shipments, 'stock': stock, 'totals': totals,
            'errors': list(dict.fromkeys(errors)), 'warnings': list(dict.fromkeys(warnings))}

