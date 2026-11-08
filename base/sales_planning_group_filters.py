"""Combined group filters and configuration status, from current model data."""
from django.core.exceptions import ValidationError

from .models import SalesPlanningGoods, SalesPlanningBlockPair
from .sales_planning_scope import split_goods, split_groups
from .sales_planning_group_service import group_key, pair_error, block_kind


ATTRIBUTES = [('brand', 'Бренд'), ('product_group', 'Товарная группа'),
              ('sales_plan_group', 'Группа планов продаж'), ('power_class', 'Мощность'),
              ('outdoor_block_type', 'INV / On-Off')]
STATUSES = [('ready', 'Настроены'), ('pending', 'Не настроены'), ('review', 'Требуют проверки')]


def filter_groups(queryset, query):
    selected = {field: list(dict.fromkeys(value for value in query.getlist(field) if value))
                for field, label in [*ATTRIBUTES, ('pair_status', '')]}
    if any(len(values) > 100 or any(len(value) > 1000 for value in values) for values in selected.values()):
        raise ValidationError('Слишком много значений фильтра или слишком длинное значение.')
    if any(value not in dict(STATUSES) for value in selected['pair_status']):
        raise ValidationError('Выберите корректный статус настройки пар.')
    choices = {field: set() for field, label in ATTRIBUTES}
    state, matches = {}, set()
    fields = ['planning_group', 'block_type', *choices]
    for row in split_goods().order_by().values(*fields).iterator():
        for field in choices:
            if row[field].strip():
                choices[field].add(row[field].strip())
        if not row['planning_group'].strip():
            continue
        key = group_key(row['planning_group'])
        group = state.setdefault(key, {'powers': set(), 'valid': set(), 'review': False})
        power = row['power_class'].strip()
        if not power or not block_kind(row['block_type']):
            group['review'] = True
        if power:
            group['powers'].add(power)
        # OR within one filter; AND across attributes of the same source model.
        if all(not selected[field] or row[field].strip() in selected[field] for field in choices):
            matches.add(key)
    for pair in SalesPlanningBlockPair.objects.filter(group__in=split_groups()).select_related('group', 'indoor', 'outdoor').iterator():
        group = state.setdefault(pair.group.source_key, {'powers': set(), 'valid': set(), 'review': False})
        if pair_error(pair):
            group['review'] = True
        else:
            group['valid'].add(pair.power)
    statuses = {key: 'review' if value['review'] else 'ready'
                if value['powers'] and value['powers'] <= value['valid'] else 'pending'
                for key, value in state.items()}
    if any(selected[field] for field in choices):
        queryset = queryset.filter(source_key__in=matches)
    if selected['pair_status']:
        if 'pending' in selected['pair_status']:
            queryset = queryset.exclude(source_key__in=[key for key, status in statuses.items()
                                                        if status not in selected['pair_status']])
        else:
            queryset = queryset.filter(source_key__in=[key for key, status in statuses.items()
                                                       if status in selected['pair_status']])
    filters = [{'name': field, 'label': label, 'caption': ', '.join(selected[field]) or 'Все доступные',
                'options': [{'value': value, 'label': value, 'selected': value in selected[field]}
                            for value in sorted(choices[field] | set(selected[field]))]}
               for field, label in ATTRIBUTES]
    filters.append({'name': 'pair_status', 'label': 'Статус настройки пар',
                    'caption': ', '.join(dict(STATUSES)[value] for value in selected['pair_status']) or 'Все статусы',
                    'options': [{'value': value, 'label': label, 'selected': value in selected['pair_status']}
                                for value, label in STATUSES]})
    return queryset, filters, statuses, selected


def page_link(query, parameter, number):
    result = query.copy()
    result[parameter] = str(number)
    return '?' + result.urlencode()


