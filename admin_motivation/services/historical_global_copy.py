"""Audited administrator override, called inside the copy transaction."""
from django.core.exceptions import ValidationError
from django.db import models
from ..models import (GlobalCoeff, SubdivisionCoeff, GlobalDirectorCoeff,
    SubdivisionDirectorCoeff, CoefficientGroupCopyLog, GlobalCoeffVersion, touch_version)


def copy_to_branches(version, branches, type_ids, recipients, user):
    GlobalCoeffVersion.objects.select_for_update().get(pk=version.pk)
    source = list(GlobalCoeff.objects.filter(version=version, type_coeff_id__in=type_ids))
    directors = list(GlobalDirectorCoeff.objects.filter(version=version))
    if type_ids and not source:
        raise ValidationError('В глобальной группе нет сохранённых коэффициентов.')
    if any(recipients.values()) and not directors:
        raise ValidationError('В глобальной группе директоров нет сохранённых коэффициентов.')
    for branch in branches:
        changes = []
        jobs = []
        if type_ids:
            jobs.append((SubdivisionCoeff, {'subdivision': branch, 'type_coeff_id__in': type_ids}, {'subdivision': branch}, source))
        for employee in recipients.get(branch.pk, []):
            jobs.append((SubdivisionDirectorCoeff, {'subdivision': branch, 'employee': employee}, {'subdivision': branch, 'employee': employee}, directors))
        for model, filters, scope, rows in jobs:
            target = model.objects.filter(version=version, **filters)
            before = list(target.values())
            # Bypass draft guards only within this authorized, audited operation.
            models.QuerySet.delete(target)
            after = []
            for original in rows:
                values = {field.attname: getattr(original, field.attname) for field in original._meta.fields if field.name not in ('id', 'version')}
                row = model(version=version, **scope, **values)
                row.full_clean()
                models.Model.save(row)
                after.append({key: str(value) for key, value in values.items()})
            changes.append({'goods': 0, 'employee': scope.get('employee').pk if scope.get('employee') else None,
                'before': [{key: str(value) for key, value in item.items()} for item in before], 'after': after})
        if changes:
            CoefficientGroupCopyLog.objects.create(version=version, subdivision=branch, actor=user.get_username(),
                source_group='global', target_group='branch', changes=changes)
    touch_version(version.pk)
