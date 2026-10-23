"""Narrow, audited exception for approved subdivision period corrections."""
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import models, transaction
from base.access import can_access
from ..models import GlobalCoeffVersion, SubdivisionManagerCoeff, KindManagerCoeff, ManagerCorrectionLog


@transaction.atomic
def update(actor, version_id, subdivision, revision, reason, changes):
    if not can_access(actor, 'admin'):
        raise PermissionDenied
    version = GlobalCoeffVersion.objects.select_for_update().get(pk=version_id)
    if version.status != 'approved':
        raise ValidationError('Этот режим доступен только для утверждённой версии.')
    if str(version.revision) != str(revision):
        raise ValidationError('Коэффициенты изменились. Обновите страницу и повторите изменение.')
    reason = (reason or '').strip()
    if not reason or len(reason) > 2000:
        raise ValidationError('Укажите причину изменения (до 2000 символов).')
    from ..views import coefficient_number
    kinds = {str(kind.pk): kind for kind in KindManagerCoeff.objects.all()}
    if not changes or set(changes) - set(kinds):
        raise ValidationError('Выберите допустимые периоды поправки.')
    values = {key: coefficient_number(value) for key, value in changes.items()}
    current = {str(row.kind_id): row for row in SubdivisionManagerCoeff.objects.select_for_update().filter(version=version, subdivision=subdivision)}
    logged = []
    for key, value in values.items():
        row = current.get(key)
        old = row.coeff if row else 1
        if old == value:
            continue
        logged.append({'kind_id': int(key), 'period': kinds[key].name, 'before': old, 'after': value})
        # Bypass the draft-only queryset solely for this validated, locked exception.
        raw = models.QuerySet(model=SubdivisionManagerCoeff)
        if row:
            raw.filter(pk=row.pk).update(coeff=value)
        else:
            raw.bulk_create([SubdivisionManagerCoeff(version=version, subdivision=subdivision, kind=kinds[key], coeff=value)])
    if not logged:
        raise ValidationError('Поправочные коэффициенты не изменились.')
    ManagerCorrectionLog.objects.create(version=version, subdivision=subdivision, actor=actor, reason=reason,
        changes=logged, previous_revision=version.revision)
    GlobalCoeffVersion.objects.filter(pk=version.pk).update(revision=models.F('revision') + 1)
    return logged
