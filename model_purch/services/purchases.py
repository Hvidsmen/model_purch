"""Shared purchase directory and coverage of the goods' purchase column."""
from ..goods_identity import planning_group_key
from ..models import PGGoods, Purch


def missing_purchases(goods=None):
    goods = PGGoods.objects.all() if goods is None else goods
    known = set(Purch.objects.values_list('name_key', flat=True))
    missing = {}
    blank_count = 0
    for name in goods.values_list('purch', flat=True):
        name = (name or '').strip()
        if not name:
            blank_count += 1
        elif planning_group_key(name) not in known:
            missing.setdefault(planning_group_key(name), name)
    return sorted(missing.values(), key=str.casefold), blank_count


def coverage_errors(scenario):
    names, blank_count = missing_purchases(PGGoods.objects.filter(scenario_plan=scenario))
    errors = [f'Закупка «{name}»: нет записи в общем справочнике закупок.' for name in names]
    if blank_count:
        errors.append(f'Не заполнена колонка «Закупка» у товаров: {blank_count}.')
    return errors
