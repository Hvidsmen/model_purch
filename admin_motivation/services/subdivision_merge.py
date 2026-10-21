"""Reviewed reference merge; historical coefficient rows and plan inputs stay intact."""
from collections import defaultdict
from contextlib import ExitStack
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from ..models import (Subdivision, SubdivisionCoeff, SubdivisionDirectorCoeff, SubdivisionManagerCoeff,
                      SubdivisionMergeLog, SalesPlanScenario, SalesPlanLine)
from .directors import lock_editable
from .plan_operations import scenario_operation

SPECS = [(SubdivisionCoeff, ('goods_id', 'type_coeff_id', 'segment_id'), ('motivation_coeff', 'manager_coeff', 'variation_calculate_id')),
         (SubdivisionDirectorCoeff, ('goods_id', 'employee_id'), ('k0', 'k1', 'k2', 'k3', 'k4')),
         (SubdivisionManagerCoeff, ('kind_id',), ('coeff',))]


def preview(source_id, target_id, version_id):
    if str(source_id) == str(target_id):
        raise ValidationError('Выберите два разных подразделения.')
    source = Subdivision.objects.get(pk=source_id)
    target = Subdivision.objects.get(pk=target_id)
    if source.merged_sources.exists():
        raise ValidationError('Это подразделение уже принимает другие подразделения. Выберите его получателем.')
    if target.merged_sources.exists():
        raise ValidationError('Получатель уже объединён с другим подразделением. Повторное объединение пока не поддерживается.')
    counts = []
    for model, keys, fields in SPECS:
        old = {tuple(getattr(row, key) for key in keys): row for row in model.objects.filter(version_id=version_id, subdivision=source)}
        new = {tuple(getattr(row, key) for key in keys): row for row in model.objects.filter(version_id=version_id, subdivision=target)}
        conflicts = sum(key in new and any(getattr(row, field) != getattr(new[key], field) for field in fields) for key, row in old.items())
        counts.append({'group': str(model._meta.verbose_name_plural), 'source': len(old), 'target': len(new), 'conflicts': conflicts})
    return {'source': source, 'target': target, 'groups': counts, 'conflicts': sum(row['conflicts'] for row in counts),
            'directors': source.directors.count(), 'plan_lines': SalesPlanLine.objects.filter(subdivision=source.subdivision_key).count()}


def apply(source_id, target_id, version_id, policy):
    if policy not in ('target', 'source'):
        raise ValidationError('Выберите приоритет коэффициентов при совпадении.')
    with ExitStack() as stack:
        for pk in SalesPlanScenario.objects.filter(is_deleted=False).values_list('pk', flat=True):
            stack.enter_context(scenario_operation(pk))
        with transaction.atomic():
            version = lock_editable(int(version_id))
            list(Subdivision.all_objects.select_for_update().filter(pk__in=[source_id, target_id]))
            info = preview(source_id, target_id, version.pk)
            source, target = info['source'], info['target']
            for model, keys, fields in SPECS:
                for row in model.objects.filter(version=version, subdivision=source):
                    identity = {key: getattr(row, key) for key in keys}
                    found = model.objects.filter(version=version, subdivision=target, **identity).first()
                    if found is None:
                        model.objects.create(version=version, subdivision=target, **identity,
                                             **{field: getattr(row, field) for field in fields})
                    elif policy == 'source':
                        for field in fields:
                            setattr(found, field, getattr(row, field))
                        found.save()
                model.objects.filter(version=version, subdivision=source).delete()
            for employee in source.directors.all():
                employee.subdivisions.add(target)
                employee.subdivisions.remove(source)
            source.merged_into = target
            source.merge_policy = policy
            source.merged_at = timezone.now()
            source.save(update_fields=['merged_into', 'merge_policy', 'merged_at'])
            # Do not change signed-off plan snapshots. Non-frozen outputs must be recalculated.
            affected = SalesPlanScenario.objects.filter(lines__subdivision__in=[source.subdivision_key, target.subdivision_key]).distinct()
            mutable = [row.pk for row in affected if not row.motivationapproval_set.exists()]
            SalesPlanLine.objects.filter(scenario_id__in=mutable).update(policies_usd=None, sales_usd=None, directors_usd=None, total_usd=None, calculation={})
            SalesPlanScenario.objects.filter(pk__in=mutable).update(calculated_at=None, summary_signature='')
            log = SubdivisionMergeLog.objects.create(source=source, target=target, version=version, policy=policy,
                details={'groups': info['groups'], 'plans_to_recalculate': len(mutable), 'sql_synced': False})
            transaction.on_commit(cache.clear)
            return log
