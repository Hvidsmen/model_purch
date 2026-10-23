"""Dated global coefficients; copy a complete version and freeze its predecessor."""
from zoneinfo import ZoneInfo
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from ..models import DiscountSegment, GlobalCoeff, GlobalCoeffVersion, SubdivisionCoeff, SubdivisionManagerCoeff, KindManagerCoeff, GlobalDirectorCoeff, SubdivisionDirectorCoeff


def today():
    return timezone.now().astimezone(ZoneInfo('Europe/Moscow')).date()


def latest_version():
    return GlobalCoeffVersion.objects.exclude(status='superseded').order_by('-pk').first()


def effective_version(on_date=None):
    return GlobalCoeffVersion.objects.filter(status='approved', effective_from__lte=on_date or today()).order_by('-effective_from', '-pk').first()


def effective_coefficients(on_date=None):
    return GlobalCoeff.objects.filter(version=effective_version(on_date))


def create_version(effective_from, title='', source_version=None, allow_overwrite=False):
    with transaction.atomic():
        latest = GlobalCoeffVersion.objects.select_for_update().exclude(status='superseded').order_by('-pk').first()
        if latest and source_version != latest.pk:
            raise ValidationError('Появилась новая версия. Обновите страницу перед созданием следующей.')
        version = GlobalCoeffVersion(effective_from=effective_from, title=title)
        version._allow_overwrite = allow_overwrite
        version.save()
        if latest:
            GlobalCoeff.objects.bulk_create([
                GlobalCoeff(version=version, goods_id=c.goods_id, type_coeff_id=c.type_coeff_id, segment_id=c.segment_id,
                            motivation_coeff=c.motivation_coeff, manager_coeff=c.manager_coeff,
                            variation_calculate_id=c.variation_calculate_id)
                for c in latest.coefficients.all()
            ])
            SubdivisionCoeff.objects.bulk_create([
                SubdivisionCoeff(version=version, subdivision_id=c.subdivision_id, goods_id=c.goods_id,
                                 type_coeff_id=c.type_coeff_id, segment_id=c.segment_id,
                                 motivation_coeff=c.motivation_coeff, manager_coeff=c.manager_coeff,
                                 variation_calculate_id=c.variation_calculate_id)
                for c in latest.subdivision_coefficients.all()
            ])
            SubdivisionManagerCoeff.objects.bulk_create([
                SubdivisionManagerCoeff(version=version, subdivision_id=c.subdivision_id, kind_id=c.kind_id, coeff=c.coeff)
                for c in latest.manager_coefficients.all()
            ])
            for model in (GlobalDirectorCoeff, SubdivisionDirectorCoeff):
                scope_fields = ['subdivision_id', 'employee_id'] if model is SubdivisionDirectorCoeff else []
                model.objects.bulk_create([model(version=version, goods_id=row.goods_id,
                    **{field: getattr(row, field) for field in scope_fields},
                    **{f'k{i}': getattr(row, f'k{i}') for i in range(5)})
                    for row in model.objects.filter(version=latest)])
        if latest:
            DiscountSegment.objects.bulk_create([DiscountSegment(version=version, type_discount=row.type_discount, **{f'o{i}': getattr(row, f'o{i}') for i in range(5)}) for row in latest.discount_segments.all()])
        return version


@transaction.atomic
def apply_to_subdivisions(version, subdivisions, groups=None, employee=None, employees_by_subdivision=None, user=None):
    from base.access import can_access
    historical = not version.is_editable
    if historical and not (user and can_access(user, 'admin')):
        raise ValidationError('Историческая версия доступна только для просмотра.')
    from .coefficient_groups import selected_groups
    from ..models import TypeCoeff
    selected = selected_groups(groups)
    names = {'policies': 'политики', 'sales': 'продажи'}
    type_ids = [kind.pk for kind in TypeCoeff.objects.all() if kind.type_coeff_name.strip().casefold() in {names[group] for group in selected if group in names}]
    subdivisions = list(subdivisions)
    recipients = {}
    skipped = []
    if 'directors' in selected:
        for sub in subdivisions:
            recipients[sub.pk] = list(employees_by_subdivision.get(sub.pk, [])) if employees_by_subdivision is not None else ([employee] if employee else [])
            if employees_by_subdivision is not None and not recipients[sub.pk] and not sub.directors.exists():
                skipped.append(str(sub))
                continue
            if not recipients[sub.pk] or any(not item.subdivisions.filter(pk=sub.pk).exists() for item in recipients[sub.pk]):
                raise ValidationError(f'Выберите директоров, привязанных к филиалу {sub}.')
        if not type_ids and not any(recipients.values()):
            raise ValidationError('В выбранных подразделениях нет директоров для копирования. Добавьте их в справочник или выберите другие подразделения.')
    if historical:
        from .historical_global_copy import copy_to_branches
        copy_to_branches(version, subdivisions, type_ids, recipients, user)
        return skipped
    for sub in subdivisions:
        if type_ids:
            for kind in KindManagerCoeff.objects.all():
                SubdivisionManagerCoeff.objects.get_or_create(version=version, subdivision=sub, kind=kind, defaults={'coeff': 1})
            # Replace only selected groups. Other groups and employees retain their values.
            SubdivisionCoeff.objects.filter(version=version, subdivision=sub, type_coeff_id__in=type_ids).delete()
            SubdivisionCoeff.objects.bulk_create([
                SubdivisionCoeff(version=version, subdivision=sub, goods_id=c.goods_id, type_coeff_id=c.type_coeff_id, segment_id=c.segment_id,
                                 motivation_coeff=c.motivation_coeff, manager_coeff=c.manager_coeff,
                                 variation_calculate_id=c.variation_calculate_id)
                for c in version.coefficients.filter(type_coeff_id__in=type_ids)
            ])
        if 'directors' in selected:
            from .directors import copy_directors
            for recipient in recipients[sub.pk]:
                copy_directors(version.pk, subdivision=sub, employee=recipient, from_global=True)
    return skipped


@transaction.atomic
def delete_draft(version_id):
    """Remove only an unused draft and its copied coefficients; retain history."""
    from datetime import date
    from django.db import models
    from django.db.models.deletion import ProtectedError
    # Same lock order as approval and creation to prevent a concurrent approval.
    versions = list(GlobalCoeffVersion.objects.select_for_update().order_by('pk'))
    version = next((item for item in versions if item.pk == version_id), None)
    if version is None:
        raise ValidationError('Версия уже удалена. Обновите страницу.')
    if version.status != GlobalCoeffVersion.Status.DRAFT:
        raise ValidationError('Удалить можно только черновик. Утверждённые и архивные версии сохраняются в истории.')
    if version.effective_from == date(2001, 1, 1):
        raise ValidationError('Исходная версия с 01.01.2001 должна сохраняться.')
    # Bypass edit-only guards for older drafts, after locking and checking status.
    # All deletions roll back if a protected result/baseline still references it.
    try:
        models.QuerySet.delete(version.discount_segments.all())
        models.QuerySet.delete(version.coefficients.all())
        models.QuerySet.delete(version.subdivision_coefficients.all())
        models.QuerySet.delete(version.manager_coefficients.all())
        models.QuerySet.delete(GlobalDirectorCoeff.objects.filter(version=version))
        models.QuerySet.delete(SubdivisionDirectorCoeff.objects.filter(version=version))
        models.QuerySet.delete(GlobalCoeffVersion.objects.filter(pk=version.pk))
    except ProtectedError:
        raise ValidationError('Версия используется в результатах расчёта, утверждения или истории копирования. Удаление запрещено.')
    return latest_version()


@transaction.atomic
def copy_from_version(destination_id, source_id, subdivision=None, employee=None):
    versions = list(GlobalCoeffVersion.objects.select_for_update().order_by('pk'))
    destination = next((v for v in versions if v.pk == destination_id), None)
    source = next((v for v in versions if v.pk == source_id), None)
    latest = next((v for v in reversed(versions) if v.status != 'superseded'), None)
    if not destination or destination.status != 'draft' or destination.pk != latest.pk:
        raise ValidationError('Копировать можно только в последний черновик.')
    if not source or source.pk == destination.pk:
        raise ValidationError('Выберите другую существующую версию.')
    model = SubdivisionCoeff if subdivision else GlobalCoeff
    scope = {'subdivision': subdivision} if subdivision else {}
    rows = list(model.objects.filter(version=source, **scope))
    director_model = SubdivisionDirectorCoeff if subdivision else GlobalDirectorCoeff
    director_scope = {'subdivision': subdivision, 'employee': employee} if subdivision else {}
    if subdivision and employee and not employee.subdivisions.filter(pk=subdivision.pk).exists():
        raise ValidationError('Сотрудник должен быть привязан к выбранному филиалу.')
    directors = list(director_model.objects.filter(version=source, **director_scope)) if not subdivision or employee else []
    if not rows and not directors:
        raise ValidationError('В выбранной версии нет коэффициентов для этой страницы.')
    model.objects.filter(version=destination, **scope).delete()
    model.objects.bulk_create([model(version=destination, **scope, goods_id=c.goods_id,
        type_coeff_id=c.type_coeff_id, segment_id=c.segment_id,
        motivation_coeff=c.motivation_coeff, manager_coeff=c.manager_coeff,
        variation_calculate_id=c.variation_calculate_id) for c in rows])
    if not subdivision or employee:
        director_model.objects.filter(version=destination, **director_scope).delete()
        director_model.objects.bulk_create([director_model(version=destination, goods_id=row.goods_id, **director_scope,
            **{f'k{i}': getattr(row, f'k{i}') for i in range(5)}) for row in directors])
    if subdivision:
        managers = list(SubdivisionManagerCoeff.objects.filter(version=source, subdivision=subdivision))
        SubdivisionManagerCoeff.objects.filter(version=destination, subdivision=subdivision).delete()
        SubdivisionManagerCoeff.objects.bulk_create([SubdivisionManagerCoeff(version=destination,
            subdivision=subdivision, kind_id=c.kind_id, coeff=c.coeff) for c in managers])
    return len(rows) + 5 * len(directors)
