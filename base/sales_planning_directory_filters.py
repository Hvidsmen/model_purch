"""Local-only filtering and pagination of the two DataWH directories."""
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Q
from .models import SalesPlanningChannel
from .sales_planning_scope import split_goods
from .sales_planning_group_filters import page_link


FIELDS = [('planning_group', 'Группа планирования'), ('sales_plan_group', 'Группа планов продаж'),
          ('brand', 'Марка'), ('power_class', 'Мощность'), ('block_type', 'Тип блока'),
          ('outdoor_block_type', 'INV / On-Off'), ('manufacturer', 'Производитель / завод')]


def filtered_directory(kind, query, *, goods=None):
    search = query.get('q', '').strip()
    status = query.get('status', 'active')
    if len(search) > 200 or status not in ('active', 'inactive', 'all'):
        raise ValidationError('Проверьте строку поиска (до 200 знаков) и статус активности.')
    base = (split_goods(include_inactive=True) if goods is None else goods) if kind == 'goods' else SalesPlanningChannel.objects.all()
    rows = base
    if status != 'all':
        rows = rows.filter(is_active=status == 'active')
    if search:
        condition = Q(name__icontains=search)
        if kind == 'goods':
            for field in ('source_key', *[field for field, label in FIELDS]):
                condition |= Q(**{field + '__icontains': search})
        rows = rows.filter(condition)
    filters = []
    if kind == 'goods':
        choices = {field: set() for field, label in FIELDS}
        for entry in base.order_by().values(*choices).iterator():
            for field, value in entry.items():
                if value:
                    choices[field].add(value)
        for field, label in FIELDS:
            selected = query.get(field, '')
            if len(selected) > 1000:
                raise ValidationError('Слишком длинное значение фильтра.')
            if selected:
                rows = rows.filter(**{field: selected})
                choices[field].add(selected)
            filters.append({'name': field, 'label': label, 'selected': selected,
                            'options': sorted(choices[field])})
    page = Paginator(rows.order_by('name', 'pk'), 50).get_page(query.get('page'))
    return {'page': page, 'total_count': base.count(), 'filters': filters, 'q': search, 'status': status,
            'previous_url': page_link(query, 'page', page.previous_page_number()) if page.has_previous() else '',
            'next_url': page_link(query, 'page', page.next_page_number()) if page.has_next() else ''}
