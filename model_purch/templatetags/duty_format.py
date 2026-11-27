from decimal import Decimal
from django import template

register = template.Library()


@register.filter
def duty_percent(value):
    return Decimal(str(value)) * 100 if value is not None else None
