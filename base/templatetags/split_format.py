from decimal import Decimal, InvalidOperation
from django import template

register = template.Library()


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
