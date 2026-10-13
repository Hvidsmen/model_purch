from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from django import template
register = template.Library()


@register.filter
def report_money(value):
    if value is None: return '—'
    try: return f'{Decimal(str(value)).quantize(Decimal(1), rounding=ROUND_HALF_UP):,}'.replace(',', '\u00a0')
    except (InvalidOperation, ValueError): return '—'


@register.filter
def report_percent(value):
    if value is None: return '—'
    try: return f'{Decimal(str(value))*100:.2f}'.replace('.', ',')+'%'
    except (InvalidOperation, ValueError): return '—'


@register.filter
def report_raw_percent(value):
    if value is None: return '—'
    try: return f'{Decimal(str(value)):.2f}'.replace('.', ',')+'%'
    except (InvalidOperation, ValueError): return '—'
