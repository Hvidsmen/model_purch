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


@register.filter
def report_cash(value):
    """Two decimal places for payroll review; legacy report rounding is unchanged."""
    if value is None:
        return '—'
    try:
        amount = Decimal(str(value)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        return f'{amount:,.2f}'.replace(',', '\u00a0').replace('.', ',')
    except (InvalidOperation, ValueError):
        return '—'
