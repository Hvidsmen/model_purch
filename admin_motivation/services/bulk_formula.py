from django.core.exceptions import ValidationError
from django.db import transaction
from ..models import GlobalCoeff, SubdivisionCoeff, VariationCalculate, TypeCoeff
from .directors import lock_editable


@transaction.atomic
def apply_formula(version_id, revision, goods_ids, variation_id, kinds, subdivision_id=None):
    version = lock_editable(int(version_id))
    if str(version.revision) != str(revision):
        raise ValidationError('Версия изменилась. Обновите страницу перед массовым применением.')
    ids = set(int(pk) for pk in goods_ids)
    if not ids:
        raise ValidationError('Отметьте товары для применения формулы.')
    if not kinds or set(kinds) - {'Политики', 'Продажи'}:
        raise ValidationError('Выберите группы коэффициентов: политики или продажи.')
    variation = VariationCalculate.objects.get(pk=variation_id)
    model = SubdivisionCoeff if subdivision_id else GlobalCoeff
    scope = {'version': version}
    if subdivision_id:
        scope['subdivision_id'] = subdivision_id
    rows = model.objects.filter(**scope)
    if ids - set(rows.values_list('goods_id', flat=True)):
        raise ValidationError('Выбран товар другой версии или подразделения.')
    type_ids = [row.pk for row in TypeCoeff.objects.all() if row.type_coeff_name.strip().casefold() in {kind.casefold() for kind in kinds}]
    selected = rows.filter(goods_id__in=ids, type_coeff_id__in=type_ids)
    if ids - set(selected.values_list('goods_id', flat=True)):
        raise ValidationError('Не у всех выбранных товаров есть коэффициенты выбранной группы.')
    count = selected.update(variation_calculate=variation)
    return len(ids), count
