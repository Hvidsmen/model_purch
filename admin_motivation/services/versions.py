"""Dated global coefficients; copy a complete version and freeze its predecessor."""
from zoneinfo import ZoneInfo
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from ..models import GlobalCoeff, GlobalCoeffVersion, SubdivisionCoeff, SubdivisionManagerCoeff, KindManagerCoeff


def today():
    return timezone.now().astimezone(ZoneInfo('Europe/Moscow')).date()


def latest_version():
    return GlobalCoeffVersion.objects.order_by('-effective_from').first()


def effective_version(on_date=None):
    return GlobalCoeffVersion.objects.filter(effective_from__lte=on_date or today()).order_by('-effective_from').first()


def effective_coefficients(on_date=None):
    return GlobalCoeff.objects.filter(version=effective_version(on_date))


def create_version(effective_from, title='', source_version=None):
    with transaction.atomic():
        latest = GlobalCoeffVersion.objects.select_for_update().order_by('-effective_from').first()
        if latest and source_version != latest.pk:
            raise ValidationError('Появилась новая версия. Обновите страницу перед созданием следующей.')
        version = GlobalCoeffVersion(effective_from=effective_from, title=title)
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
        return version


@transaction.atomic
def apply_to_subdivisions(version, subdivisions):
    if version.pk != latest_version().pk:
        raise ValidationError('Историческая версия доступна только для просмотра.')
    for sub in subdivisions:
        for kind in KindManagerCoeff.objects.all():
            SubdivisionManagerCoeff.objects.get_or_create(version=version, subdivision=sub, kind=kind, defaults={'coeff': 1})
        # Replace this subdivision's set so deleted goods cannot retain obsolete coefficients.
        SubdivisionCoeff.objects.filter(version=version, subdivision=sub).delete()
        SubdivisionCoeff.objects.bulk_create([
            SubdivisionCoeff(version=version, subdivision=sub, goods_id=c.goods_id, type_coeff_id=c.type_coeff_id, segment_id=c.segment_id,
                             motivation_coeff=c.motivation_coeff, manager_coeff=c.manager_coeff,
                             variation_calculate_id=c.variation_calculate_id)
            for c in version.coefficients.all()
        ])
