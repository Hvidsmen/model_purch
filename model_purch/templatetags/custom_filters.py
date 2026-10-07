from django import template
import re

register = template.Library()


@register.filter
def clean_number(value):
    """
    Очищает число от символов валют и лишних знаков.
    """
    if value is None:
        return 0.0

    str_value = str(value)
    cleaned = re.sub(r'[^\d.\-]', '', str_value)

    try:
        return float(cleaned)
    except (ValueError, TypeError):
        return 0.0


@register.filter
def format_money(value, decimals=2):
    """
    Форматирует число как денежную сумму с разделителями тысяч.
    """
    try:
        num = float(value)
        formatted = f"{num:,.{decimals}f}"
        return formatted.replace(',', ' ')
    except (ValueError, TypeError):
        return "0.00"


@register.filter
def format_percent(value):
    """
    Форматирует процент как число (без символа %).
    Используется для value в input[type=number].
    """
    try:
        num = float(value)
        return f"{num * 100:.1f}"
    except (ValueError, TypeError):
        return "0.0"


@register.filter
def format_percent_display(value):
    """
    Форматирует процент для отображения (с символом %).
    Используется в tooltip, alert и других местах.
    """
    try:
        num = float(value)
        return f"{num * 100:.1f}%"
    except (ValueError, TypeError):
        return "0.0%"


@register.filter
def format_integer(value):
    """
    Форматирует целое число с разделителями тысяч.
    """
    try:
        num = int(value)
        return f"{num:,}".replace(',', ' ')
    except (ValueError, TypeError):
        return "0"