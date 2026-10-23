from django.core.exceptions import ValidationError
from django.db import transaction
from ..models import GlobalCoeff, SubdivisionCoeff, VariationCalculate, TypeCoeff, GlobalDirectorCoeff, SubdivisionDirectorCoeff, DirectorEmployee
from .directors import lock_editable


def selection(version_id, revision, goods_ids, variation_id, kinds, subdivision_id=None, employee_id=None):
    version = lock_editable(int(version_id))
    if str(version.revision) != str(revision):
        raise ValidationError('Версия изменилась. Обновите страницу перед массовым применением.')
    ids = set(int(pk) for pk in goods_ids)
    if not ids:
        raise ValidationError('Отметьте товары для применения формулы.')
    if not kinds or set(kinds) - {'Политики', 'Продажи', 'Директора'}:
        raise ValidationError('Выберите группы коэффициентов: политики, продажи или директор.')
    variation = VariationCalculate.objects.get(pk=variation_id)
    model = SubdivisionCoeff if subdivision_id else GlobalCoeff
    scope = {'version': version}
    if subdivision_id:
        scope['subdivision_id'] = subdivision_id
    updates = []
    for kind in set(kinds) - {'Директора'}:
        type_ids = [row.pk for row in TypeCoeff.objects.all() if row.type_coeff_name.strip().casefold() == kind.casefold()]
        selected = model.objects.filter(**scope, goods_id__in=ids, type_coeff_id__in=type_ids)
        if ids - set(selected.values_list('goods_id', flat=True)):
            raise ValidationError(f'Не у всех выбранных товаров есть коэффициенты группы «{kind}» в этой версии и подразделении.')
        updates.append(selected)
    if 'Директора' in kinds:
        director_model = SubdivisionDirectorCoeff if subdivision_id else GlobalDirectorCoeff
        director_scope = dict(scope)
        if subdivision_id:
            if not employee_id or not DirectorEmployee.objects.filter(pk=employee_id, is_archived=False, subdivisions=subdivision_id).exists():
                raise ValidationError('Выберите директора, привязанного к этому подразделению.')
            director_scope['employee_id'] = employee_id
        selected = director_model.objects.filter(**director_scope, goods_id__in=ids)
        if ids - set(selected.values_list('goods_id', flat=True)):
            raise ValidationError('Сначала сохраните директорские ставки для всех выбранных товаров. Формула не применена; нулевые ставки автоматически не создаются.')
        updates.append(selected)
    return version, variation, ids, updates


@transaction.atomic
def preview_formula(*args, **kwargs):
    version, variation, ids, updates = selection(*args, **kwargs)
    from ..models import Goods
    return {'version':str(version),'formula':str(variation),'goods_count':len(ids),
            'goods':list(Goods.objects.filter(pk__in=ids).order_by('brand').values_list('brand',flat=True)[:100]),
            'rows':sum(rows.count() for rows in updates),
            'changes':sum(rows.exclude(variation_calculate=variation).count() for rows in updates)}


@transaction.atomic
def apply_formula(*args, **kwargs):
    version, variation, ids, updates = selection(*args, **kwargs)
    count = sum(rows.update(variation_calculate=variation) for rows in updates)
    return len(ids), count
