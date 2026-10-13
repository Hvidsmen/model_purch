from decimal import Decimal
from django.core.exceptions import ValidationError
from ..models import (GlobalCoeff, GlobalDirectorCoeff, Goods, TypeCoeff, SegmentCoeff)

GROUP_NAMES = {'policies': 'Политики', 'sales': 'Продажи', 'directors': 'Директор'}


def selected_groups(values, default=('policies', 'sales')):
    groups = set(values or default)
    if not groups or not groups <= GROUP_NAMES.keys():
        raise ValidationError('Выберите группу коэффициентов: политики, продажи или директор.')
    return groups


def global_matrix(version):
    matrix = GlobalCoeff.get_matrix_str(version) if version else {}
    directors = {row.goods_id: row for row in GlobalDirectorCoeff.objects.filter(version=version).select_related('goods')}
    types = list(TypeCoeff.objects.order_by('pk'))
    segments = list(SegmentCoeff.objects.order_by('pk'))
    for row in directors.values():
        group = matrix.setdefault(row.goods.planning_group_sales, {}).setdefault(row.goods.group, {})
        if row.goods not in group:
            group[row.goods] = [[None for _ in segments] for _ in types]
    for groups in matrix.values():
        for goods in groups.values():
            for good in goods:
                row = directors.get(good.pk)
                good.director_cells = [{'index': i, 'value': getattr(row, f'k{i}') if row else Decimal(0)} for i in range(5)]
    return matrix


def save_global_directors(version, data, deleted=()):
    from ..views import coefficient_number
    changes = {}
    for key in data:
        if not key.startswith('director_coeff='):
            continue
        parts = key.split('=')
        if len(parts) != 3 or not parts[1].isdigit() or parts[2] not in {'0', '1', '2', '3', '4'}:
            raise ValidationError('Некорректная ячейка коэффициентов директора.')
        pk = int(parts[1])
        if str(pk) in {str(value) for value in deleted}:
            continue
        changes.setdefault(pk, {})[f'k{parts[2]}'] = Decimal(str(coefficient_number(data[key])))
    if len(set(Goods.objects.filter(pk__in=changes).values_list('pk', flat=True))) != len(changes):
        raise ValidationError('Товарная матрица не найдена.')
    for goods_id, values in changes.items():
        row, _ = GlobalDirectorCoeff.objects.get_or_create(version=version, goods_id=goods_id)
        for key, value in values.items():
            setattr(row, key, value)
        row.save()
    GlobalDirectorCoeff.objects.filter(version=version, goods_id__in=deleted).delete()
