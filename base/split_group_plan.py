"""Resize one group's period plan using saved channel proportions."""
from django.core.exceptions import ValidationError

from .split_engine import ZERO, allocate, number, planning_months, precise, quantity, stored


@precise
def set_period_quantity(scenario, state, line, previous, value):
    value = number(value)
    if line.get('allocation') == 'quantity' and value != quantity(scenario, previous):
        months = planning_months(scenario)
        monthly = [number(previous.get('month_quantities', {}).get(month), optional=True) for month in months]
        if value and (any(item is None for item in monthly) or not sum(monthly, ZERO)):
            raise ValidationError('Нет сохранённых месячных пропорций канала. Заполните распределение по месяцам.')
        line['month_quantities'] = dict(zip(months, map(stored,
            allocate(value, monthly) if value else [ZERO] * len(months))))
    actual = number(line.get('fact'), optional=True)
    if state.get('volume_mode', scenario.entry_mode) == 'annual' and actual is None and value:
        raise ValidationError('Для годовой цели заполните состоявшийся факт до начала плана.')
    line['input'] = stored(value + ((actual or ZERO) if state.get('volume_mode', scenario.entry_mode) == 'annual' else ZERO))
    number(line['input'])


@precise
def distribute(scenario, state, gid, target, method='saved'):
    if gid not in state['groups']:
        raise ValidationError('Группа отсутствует в плане. Обновите страницу.')
    target = number(target)
    group = state['groups'][gid]
    previous = scenario.state['groups'][gid]['channels']
    fixed = sum((quantity(scenario, line) for line in group['channels'].values()
                 if line['lock_qty']), ZERO)
    free = [(cid, line) for cid, line in group['channels'].items() if not line['lock_qty']]
    if method not in ('saved','equal'):raise ValidationError('Выберите способ распределения каналов.')
    weights = [number(1) if method=='equal' else quantity(scenario, previous[cid]) for cid, _ in free]
    if target < fixed or (not free and target != fixed):
        raise ValidationError('Общий план несовместим с закреплёнными объёмами каналов.')
    if target > fixed and not sum(weights, ZERO):
        raise ValidationError('Нет сохранённых пропорций каналов. Сначала задайте и сохраните объёмы по каналам.')
    values = allocate(target - fixed, weights) if sum(weights, ZERO) else [ZERO] * len(free)
    for (cid, line), value in zip(free, values):
        set_period_quantity(scenario, state, line, previous[cid], value)
    return state
