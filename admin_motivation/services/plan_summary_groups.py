"""Persistent exact plan inputs grouped at coefficient version boundaries."""
import hashlib
import json
from .subdivision_scope import included
from decimal import Decimal
from django.core.exceptions import ValidationError
from django.db import transaction, connection, OperationalError
from django.db.models import F
from ..models import SalesPlanScenario, SalesPlanSummaryGroup, GlobalCoeffVersion

SEGMENTS = tuple(f'O{i}' for i in range(5))
ZERO = Decimal(0)


def summary_versions():
    versions = GlobalCoeffVersion.objects.exclude(status='superseded')
    if versions.filter(status='approved').exists():
        versions = versions.filter(status='approved')
    return list(versions.order_by('effective_from', 'pk'))


def signature(versions):
    from .subdivision_aliases import rules
    from .subdivision_scope import POLICY
    boundaries = [(v.pk, v.effective_from.isoformat()) for v in versions]
    boundaries.append(POLICY)
    if rules():
        boundaries.append(rules())
    return hashlib.sha256(json.dumps(boundaries).encode()).hexdigest()


def ensure_summary_groups(scenario, versions=None, force=False):
    from .sales_plans import number
    versions = summary_versions() if versions is None else versions
    wanted = signature(versions)
    current = SalesPlanScenario.objects.values_list('summary_signature', flat=True).get(pk=scenario.pk)
    if not force and current == wanted:
        scenario.summary_signature = current
        return scenario.summary_groups.all()
    try:
        return _rebuild(scenario, versions, wanted, force, number)
    except OperationalError as error:
        if connection.vendor == 'sqlite' and ('locked' in str(error).lower() or 'busy' in str(error).lower()):
            raise ValidationError('Сводные данные сейчас обновляются. Повторите открытие отчёта после завершения подготовки плана.') from error
        raise


@transaction.atomic
def _rebuild(scenario, versions, wanted, force, number):
    # SQLite ignores SELECT FOR UPDATE: acquire its writer lock before reading
    # a million rows, so concurrent cold requests cannot both build a snapshot.
    if connection.vendor == 'sqlite':
        SalesPlanScenario.objects.filter(pk=scenario.pk).update(summary_signature=F('summary_signature'))
    locked = SalesPlanScenario.objects.select_for_update().get(pk=scenario.pk)
    if force or locked.summary_signature != wanted:
        grouped, dates = {}, {}
        lines = locked.lines.exclude(subdivision='').order_by().values_list(
            'plan_date', 'subdivision', 'planning_group_sales', 'group', 'brand',
            'amount_usd', 'amount_ddp_usd', 'segment_amounts')
        from .subdivision_aliases import mapping, canonical
        names = mapping()
        for day, sub, pg, group, brand, plan, ddp, segments in lines.iterator(chunk_size=2000):
            if not included(sub, names):
                continue
            sub = canonical(sub, names)
            if day not in dates:
                dates[day] = next((v.pk for v in reversed(versions) if v.effective_from <= day), None)
            key = (dates[day], sub, pg, group, brand)
            values = grouped.setdefault(key, [ZERO, ZERO, [ZERO] * len(SEGMENTS)])
            values[0] += plan
            values[1] = values[1] + ddp if values[1] is not None and ddp is not None else None
            for index, segment in enumerate(SEGMENTS):
                values[2][index] += number(segments.get(segment))
        # Replace only after the entire source has been read successfully.
        locked.summary_groups.all().delete()
        pending = []
        for (version, sub, pg, group, brand), (plan, ddp, segments) in grouped.items():
            pending.append(SalesPlanSummaryGroup(scenario_id=locked.pk, version_key=version,
                subdivision=sub, planning_group_sales=pg, group=group, brand=brand,
                amounts={'plan': str(plan), 'ddp': str(ddp) if ddp is not None else None,
                         'segments': [str(value) for value in segments]}))
            if len(pending) >= 1000:
                SalesPlanSummaryGroup.objects.bulk_create(pending, batch_size=1000)
                pending.clear()
        if pending:
            SalesPlanSummaryGroup.objects.bulk_create(pending, batch_size=1000)
        locked.summary_signature = wanted
        locked.save(update_fields=['summary_signature'])
    scenario.summary_signature = locked.summary_signature
    return scenario.summary_groups.all()
