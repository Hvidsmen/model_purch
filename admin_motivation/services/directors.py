from django.core.exceptions import ValidationError
from django.db import transaction
from ..models import GlobalCoeffVersion, GlobalDirectorCoeff, SubdivisionDirectorCoeff

KEYS = [f'k{i}' for i in range(5)]


def lock_editable(version_id):
    versions = list(GlobalCoeffVersion.objects.select_for_update().order_by('pk'))
    latest = next((v for v in reversed(versions) if v.status != 'superseded'), None)
    if not latest or latest.pk != version_id or latest.status != 'draft':
        raise ValidationError('Изменять можно только последний черновик версии.')
    return latest


def copy_rows(source, target_model, version, **scope):
    rows = list(source)
    if not rows:
        raise ValidationError('В источнике нет коэффициентов директоров. Текущие настройки сохранены.')
    target_model.objects.filter(version=version, **scope).delete()
    target_model.objects.bulk_create([target_model(version=version, goods_id=row.goods_id,
        **scope, **{key: getattr(row, key) for key in KEYS}) for row in rows])
    return len(rows)


@transaction.atomic
def copy_directors(version_id, source_id=None, subdivision=None, employee=None, from_global=False):
    version = lock_editable(version_id)
    model = SubdivisionDirectorCoeff if subdivision else GlobalDirectorCoeff
    scope = {'subdivision': subdivision, 'employee': employee} if subdivision else {}
    if subdivision and (not employee or not employee.subdivisions.filter(pk=subdivision.pk).exists()):
        raise ValidationError('Сотрудник должен быть привязан к выбранному филиалу.')
    if from_global:
        if not subdivision:
            raise ValidationError('Выберите филиал и сотрудника.')
        rows = GlobalDirectorCoeff.objects.filter(version=version)
    else:
        if source_id == version_id or not GlobalCoeffVersion.objects.filter(pk=source_id).exists():
            raise ValidationError('Выберите другую версию-источник.')
        rows = model.objects.filter(version_id=source_id, **scope)
    return copy_rows(rows, model, version, **scope)
