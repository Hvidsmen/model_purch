from decimal import Decimal
from django.core.exceptions import ValidationError
from ..models import (GlobalCoeff, GlobalDirectorCoeff, SubdivisionCoeff, SubdivisionDirectorCoeff, Goods, TypeCoeff, SegmentCoeff)

GROUP_NAMES = {'policies': 'Политики', 'sales': 'Продажи', 'directors': 'Директор'}


def selected_groups(values, default=('policies', 'sales')):
    groups = set(values or default)
    if not groups or not groups <= GROUP_NAMES.keys():
        raise ValidationError('Выберите группу коэффициентов: политики, продажи или директор.')
    return groups


def coefficient_matrix(version, subdivision=None, employee=None):
    matrix = SubdivisionCoeff.get_matrix_str(subdivision, version) if subdivision else (GlobalCoeff.get_matrix_str(version) if version else {})
    model = SubdivisionDirectorCoeff if subdivision else GlobalDirectorCoeff
    scope = {'subdivision': subdivision, 'employee': employee} if subdivision else {}
    queryset = model.objects.filter(version=version, **scope) if not subdivision or employee else model.objects.none()
    directors = {row.goods_id: row for row in queryset.select_related('goods', 'variation_calculate')}
    global_core = {(row['goods_id'], row['type_coeff_id'], row['segment_id']): row['motivation_coeff'] for row in GlobalCoeff.objects.filter(version=version).values('goods_id','type_coeff_id','segment_id','motivation_coeff')}
    global_directors = {row.goods_id: row for row in GlobalDirectorCoeff.objects.filter(version=version)}
    types = list(TypeCoeff.objects.order_by('pk'))
    segments = list(SegmentCoeff.objects.order_by('pk'))
    for row in directors.values():
        group = matrix.setdefault(row.goods.planning_group_sales, {}).setdefault(row.goods.group, {})
        if row.goods not in group:
            group[row.goods] = [[None for _ in segments] for _ in types]
    for groups in matrix.values():
        for goods in groups.values():
            for good in goods:
                # UI groups have fixed semantics, independent of reference insertion order.
                core = {(cell.type_coeff.type_coeff_name.strip().casefold(),cell.segment_id):cell for cells in goods[good] for cell in cells if cell is not None}
                goods[good] = [[core.get((name,segment.pk)) for segment in segments] for name in ('политики','продажи')]
                present = [cell for cells in goods[good] for cell in cells if cell is not None]
                good.editor_formula_id = present[0].variation_calculate_id if present else None
                good.editor_mixed_formulas = len({cell.variation_calculate_id for cell in present}) > 1
                for cells in goods[good]:
                    for cell in cells:
                        if cell is not None:
                            cell.editor_global_value = global_core.get((good.pk, cell.type_coeff_id, cell.segment_id))
                good.has_core_coefficients = any(cell is not None for group in goods[good] for cell in group)
                row = directors.get(good.pk)
                good.director_formula_label = row.variation_calculate.variation_name if row and row.variation_calculate_id else ''
                good.director_cells = [{'index': i, 'exists': row is not None, 'global_value': getattr(global_directors[good.pk], f'k{i}') if good.pk in global_directors else None, 'value': getattr(row, f'k{i}') if row else Decimal(0)} for i in range(5)]
    return matrix


def global_matrix(version):
    return coefficient_matrix(version)


def save_global_directors(version, data, deleted=(), subdivision=None, employee=None):
    from ..views import coefficient_number
    model = SubdivisionDirectorCoeff if subdivision else GlobalDirectorCoeff
    scope = {'subdivision': subdivision, 'employee': employee} if subdivision else {}
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
    if subdivision and changes and (not employee or not employee.subdivisions.filter(pk=subdivision.pk).exists()):
        raise ValidationError('Выберите директора, привязанного к этому филиалу.')
    if len(set(Goods.objects.filter(pk__in=changes).values_list('pk', flat=True))) != len(changes):
        raise ValidationError('Товарная матрица не найдена.')
    existing_goods = set(model.objects.filter(version=version, **scope).values_list('goods_id',flat=True))
    for goods_id, values in changes.items():
        if goods_id not in existing_goods and not any(values.values()):
            continue  # Do not turn untouched zero placeholders into exact coefficients.
        row, _ = model.objects.get_or_create(version=version, goods_id=goods_id, **scope)
        for key, value in values.items():
            setattr(row, key, value)
        row.save()
    model.objects.filter(version=version, goods_id__in=deleted, **scope).delete()
