"""Explicit administrator retraction with an immutable pre-edit snapshot."""
import json
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.serializers.json import DjangoJSONEncoder
from django.db import models, transaction
from base.access import can_access
from ..models import (GlobalCoeffVersion, GlobalCoeff, SubdivisionCoeff, SubdivisionManagerCoeff,
                      GlobalDirectorCoeff, SubdivisionDirectorCoeff, DiscountSegment, VersionReopenLog)


@transaction.atomic
def reopen_version(actor, version_id, revision, reason):
    if not can_access(actor, 'motivation_admin'):
        raise PermissionDenied
    if not reason.strip():
        raise ValidationError('Укажите причину возврата версии в черновик.')
    versions = list(GlobalCoeffVersion.objects.select_for_update().order_by('pk'))
    approved = sorted([row for row in versions if row.status == 'approved'], key=lambda row: (row.effective_from, row.pk))
    if not approved or approved[-1].pk != int(version_id):
        raise ValidationError('Вернуть в черновик можно только последнюю утверждённую версию.')
    version = approved[-1]
    if str(version.revision) != str(revision):
        raise ValidationError('Версия изменилась. Обновите страницу и повторите возврат.')
    latest = next(row for row in reversed(versions) if row.status != 'superseded')
    if latest.pk != version.pk:
        raise ValidationError('Есть более новый черновик. Сохраните нужные изменения или удалите его штатной кнопкой перед возвратом утверждённой версии.')
    snapshot = {'version': {'id': version.pk, 'title': version.title, 'effective_from': version.effective_from, 'approved_at': version.approved_at, 'revision': version.revision, 'status': version.status, 'fallback_variation': version.fallback_variation}}
    for model in (GlobalCoeff, SubdivisionCoeff, SubdivisionManagerCoeff, GlobalDirectorCoeff, SubdivisionDirectorCoeff, DiscountSegment):
        snapshot[model._meta.label] = list(model.objects.filter(version=version).order_by('pk').values())
    snapshot = json.loads(json.dumps(snapshot, cls=DjangoJSONEncoder))
    restored = [row.pk for row in versions if row.status == 'superseded' and row.replaced_by_id == version.pk]
    models.QuerySet.update(GlobalCoeffVersion.objects.filter(pk=version.pk), status='draft', approved_at=None, revision=models.F('revision') + 1)
    if restored:
        models.QuerySet.update(GlobalCoeffVersion.objects.filter(pk__in=restored), status='approved', replaced_by=None, revision=models.F('revision') + 1)
    VersionReopenLog.objects.create(version=version, actor=actor, reason=reason.strip(), snapshot=snapshot, restored_version_ids=restored)
    version.refresh_from_db()
    return version
