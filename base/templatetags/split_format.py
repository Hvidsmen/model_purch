from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext
from django import template

register = template.Library()


@register.filter
def fact_number(value):
    return '' if value == 0 else split_number(value)


@register.filter
def fact_integer(value):
    return '' if value == 0 else fixed_number(value, 0)


@register.filter
def fact_cash(value):
    return '' if value == 0 else split_revenue(value)


def fixed_number(value, places):
    if value is None:
        return '—'
    try:
        with localcontext() as context:
            context.prec = 64
            value = Decimal(str(value))
            if not value.is_finite():
                return '—'
            value = value.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
            if not value:
                value = abs(value)
            return format(value, ',.' + str(places) + 'f').replace(',', '\u00a0').replace('.', ',')
    except (ValueError, InvalidOperation):
        return '—'


@register.filter
def split_revenue(value):
    return fixed_number(value, 0)


@register.filter
def split_integer(value):
    return fixed_number(value, 0)


@register.filter
def split_markup(value):
    return fixed_number(value, 2)


@register.filter
def fact_markup(value):
    return '' if value == 0 else split_markup(value)


@register.filter
def lookup(value, key):
    return value.get(str(key)) if isinstance(value, dict) else None


@register.filter
def split_number(value):
    if value is None:
        return '—'
    try:
        raw = format(Decimal(str(value)), ',.6f').rstrip('0').rstrip('.')
        return raw.replace(',', '\u00a0').replace('.', ',')
    except (ValueError, InvalidOperation):
        return '—'
