"""Derived groups and manual Split settings have independent lifecycles."""
from hashlib import sha256
import re

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count
from django.db.models.functions import Trim
from django.utils import timezone

from .models import SalesPlanningGoods, SalesPlanningGroup, SalesPlanningDirectoryLoad, SalesPlanningBlockPair
from .sales_planning_scope import split_goods, PRODUCT_GROUP


def group_key(name):
    return sha256(name.strip().casefold().encode('utf-8')).hexdigest()


def ungrouped_goods():
    return split_goods().annotate(trimmed=Trim('planning_group')).filter(trimmed='')


@transaction.atomic
def rebuild_groups():
    if not SalesPlanningDirectoryLoad.objects.filter(directory='goods').exists():
        raise ValidationError('Сначала загрузите номенклатуру из DataWH.')
    names = {}
    for raw in split_goods().order_by().values_list('planning_group', flat=True).distinct():
        if raw.strip():
            names.setdefault(group_key(raw), []).append(raw)
    SalesPlanningGroup.objects.update(is_active=False)
    for key, aliases in names.items():
        group, created = SalesPlanningGroup.objects.get_or_create(source_key=key, defaults={'name': aliases[0].strip()})
        group.source_names, group.is_active = sorted(aliases), True
        group.save(update_fields=['source_names', 'is_active'])
    SalesPlanningDirectoryLoad.objects.update_or_create(directory='groups', defaults={
        'loaded_at': timezone.now(), 'row_count': len(names)})
    return len(names)


def group_goods(group):
    return split_goods().filter(planning_group__in=group.source_names)


def group_summaries(groups):
    """One aggregate query for the current page, preserving mixed attributes."""
    owners = {raw: group.pk for group in groups for raw in group.source_names}
    stats = {group.pk: {'count': 0, 'brand': set(), 'power_class': set(), 'block_type': set(),
                        'product_group': set(), 'outdoor_block_type': set()} for group in groups}
    fields = ['planning_group', 'brand', 'power_class', 'block_type', 'product_group', 'outdoor_block_type']
    rows = split_goods().filter(planning_group__in=list(owners)).order_by().values(*fields).annotate(count=Count('pk'))
    for row in rows:
        stat = stats[owners[row['planning_group']]]
        stat['count'] += row['count']
        for field in fields[1:]:
            if row[field].strip():
                stat[field].add(row[field].strip())
    return [{'group': group, **{key: sorted(value) if isinstance(value, set) else value
                               for key, value in stats[group.pk].items()}} for group in groups]


def block_kind(value):
    """Use the source block-type label, never the model name or INV flag."""
    key = re.sub(r'[\W_]+', '', str(value or '').casefold())
    if 'внутр' in key and 'наруж' in key:
        return None
    if key.startswith(('внутр', 'блоквнутр')) or key in ('indoor', 'indoorunit', 'in'):
        return 'indoor'
    if key.startswith(('наруж', 'блокнаруж')) or key in ('outdoor', 'outdoorunit', 'out'):
        return 'outdoor'
    return None


def pair_error(pair):
    for good, kind in [(pair.indoor, 'indoor'), (pair.outdoor, 'outdoor')]:
        if good.product_group.strip().casefold() != PRODUCT_GROUP.casefold():
            return 'Модель вне товарной группы «Бытовые кондиционеры». Проверьте пару.'
        if not good.is_active:
            return 'Выбранная модель отсутствует в текущей номенклатуре. Подберите замену.'
        if group_key(good.planning_group) != pair.group.source_key or good.power_class.strip() != pair.power:
            return 'Группа или мощность выбранной модели изменилась. Проверьте пару.'
        if block_kind(good.block_type) != kind:
            return 'В паре должен быть один внутренний и один наружный блок.'
    return ''


def power_rows(group, goods):
    powers = {}
    for good in goods:
        power = good.power_class.strip()
        if not power:
            continue
        row = powers.setdefault(power, {'power': power, 'indoor': [], 'outdoor': []})
        kind = block_kind(good.block_type)
        if kind:
            row[kind].append(good)
    for pair in group.block_pairs.select_related('indoor', 'outdoor', 'group'):
        row = powers.setdefault(pair.power, {'power': pair.power, 'indoor': [], 'outdoor': []})
        row['pair'], row['error'] = pair, pair_error(pair)
    for row in powers.values():
        for kind in ('indoor', 'outdoor'):
            row['selected_' + kind] = (getattr(row['pair'], kind + '_id') if row.get('pair')
                                      else row[kind][0].pk if len(row[kind]) == 1 else None)
        row['automatic'] = not row.get('pair') and len(row['indoor']) == len(row['outdoor']) == 1
    return [powers[key] for key in sorted(powers)]


@transaction.atomic
def save_unique_pairs(group):
    if not group.is_active:
        raise ValidationError('Группа неактивна.')
    count = 0
    for row in power_rows(group, list(group_goods(group))):
        if row['automatic']:
            save_pair(group, row['power'], row['selected_indoor'], row['selected_outdoor'])
            count += 1
    return count


@transaction.atomic
def save_pair(group, power, indoor_id, outdoor_id):
    if not group.is_active or not power.strip():
        raise ValidationError('Выберите активную группу и заполненную мощность.')
    goods = {str(good.pk): good for good in group_goods(group).filter(pk__in=[indoor_id, outdoor_id])}
    if str(indoor_id) not in goods or str(outdoor_id) not in goods:
        raise ValidationError('Оба блока должны входить в текущий состав этой группы.')
    pair = SalesPlanningBlockPair(group=group, power=power.strip(), indoor=goods[str(indoor_id)], outdoor=goods[str(outdoor_id)])
    error = pair_error(pair)
    if error:
        raise ValidationError(error)
    return SalesPlanningBlockPair.objects.update_or_create(group=group, power=pair.power,
        defaults={'indoor': pair.indoor, 'outdoor': pair.outdoor})[0]


