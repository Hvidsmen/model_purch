"""SQL receipts, manual new purchases and signed receipt corrections stay separate."""
from decimal import Decimal, InvalidOperation
from django.core.exceptions import ValidationError
from .split_engine import ZERO, HUNDRED, allocate, number, precise


def signed_quantity(value):
    try:
        result = Decimal(str(value).strip().replace(',', '.'))
    except (InvalidOperation, ValueError):
        raise ValidationError('Введите корректировку поставок числом со знаком плюс или минус.')
    number(abs(result))
    return result


def sql_receipts(power, kind):
    return number(power.get('incoming_sql_' + kind, power.get('incoming_' + kind)), optional=True)


@precise
def spread(group, target):
    keys = list(group['powers'])
    if target == ZERO:
        return dict.fromkeys(keys, ZERO)
    weights = [number(power.get('share'), optional=True) for power in group['powers'].values()]
    if not weights or any(weight is None for weight in weights) or sum(weights, ZERO) != HUNDRED:
        raise ValidationError('Для распределения новых поставок и корректировки задайте доли мощностей, сумма — 100%.')
    sign = -1 if target < ZERO else 1
    return dict(zip(keys, (value * sign for value in allocate(abs(target), weights))))


def corrections(group):
    return spread(group, signed_quantity(group.get('supply_adjustment', '0')))


def validate_corrections(group):
    for key, value in corrections(group).items():
        if not value:
            continue
        for kind in ('indoor', 'outdoor'):
            base = sql_receipts(group['powers'][key], kind)
            if base is None:
                raise ValidationError('Сначала загрузите запланированные поставки обоих блоков: ' + key)
            if base + value < ZERO:
                raise ValidationError('Корректировка превышает запланированные поставки по мощности ' + key + '. Итог поставок не может быть отрицательным.')
