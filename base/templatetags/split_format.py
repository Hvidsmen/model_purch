from decimal import Decimal, InvalidOperation
from django import template

register = template.Library()


@register.filter
def fact_number(value):
    return '' if value == 0 else split_number(value)


@register.filter
def fact_cash(value):
    from admin_motivation.templatetags.report_format import report_cash
    return '' if value == 0 else report_cash(value)


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
