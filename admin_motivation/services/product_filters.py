"""Shared product selection; filters never recompute stored payout gates."""
from django.core.exceptions import ValidationError
from ..models import Goods

FIELDS = (('planning', 'planning_group_sales', 'Группа планов продаж'),
          ('goods', 'group', 'Группа'), ('brand', 'brand', 'Бренд'))


def fields(query, *, strict=True):
    result = []
    for name, column, label in FIELDS:
        selected = list(dict.fromkeys(query.getlist(name)))
        if len(selected) > 100 or any(len(value) > 255 for value in selected):
            if strict:
                raise ValidationError('Слишком много значений товарного фильтра или слишком длинное значение.')
            selected = [value for value in selected if len(value) <= 255][:100]
        options = set(Goods.objects.order_by().values_list(column, flat=True).distinct())
        options.update(selected)
        result.append({'name': name, 'label': label, 'multiple': True,
                       'caption': ', '.join(selected) if 0 < len(selected) <= 2 else f'Выбрано: {len(selected)}' if selected else 'Все доступные',
                       'options': [{'value': value, 'selected': value in selected}
                                   for value in sorted(value for value in options if value)]})
    return result
