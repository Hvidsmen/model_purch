"""Reviewed atomic reference merge; historical rows and plan inputs stay intact."""
import hashlib
import json
from contextlib import ExitStack
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from base.models import MotivationReportAccess, PortalReportGroup
from ..models import (Subdivision, SubdivisionCoeff, SubdivisionDirectorCoeff, SubdivisionManagerCoeff,
                      SubdivisionMergeLog, SalesPlanScenario, SalesPlanLine)
from .directors import lock_editable
from .plan_operations import scenario_operation

SPECS = [(SubdivisionCoeff, ('goods_id', 'type_coeff_id', 'segment_id'), ('motivation_coeff', 'manager_coeff', 'variation_calculate_id')),
         (SubdivisionDirectorCoeff, ('goods_id', 'employee_id'), ('k0', 'k1', 'k2', 'k3', 'k4')),
         (SubdivisionManagerCoeff, ('kind_id',), ('coeff',))]
LABELS = {SubdivisionCoeff: 'Мотивация по товарной матрице', SubdivisionDirectorCoeff: 'Коэффициенты директоров', SubdivisionManagerCoeff: 'Коэффициенты менеджеров'}


def preview_many(source_ids, target_id, version_id):
    ids = sorted(set(int(value) for value in source_ids))
    if not ids:
        raise ValidationError('Выберите хотя бы одно присоединяемое подразделение.')
    if int(target_id) in ids:
        raise ValidationError('Выберите разные присоединяемые и итоговое подразделения.')
    sources = list(Subdivision.objects.filter(pk__in=ids))
    if len(sources) != len(ids):
        raise ValidationError('Одно из подразделений уже объединено или удалено. Обновите выбор.')
    sources.sort(key=lambda row: (row.subdivision_key.casefold(), row.pk))
    target = Subdivision.objects.get(pk=target_id)
    if Subdivision.all_objects.filter(merged_into_id__in=ids).exists():
        raise ValidationError('Присоединяемое подразделение уже принимает другие подразделения. Выберите его получателем.')
    branches = [target, *sources]
    counts, state = [], [(row.pk, row.subdivision_key) for row in branches]
    for model, keys, fields in SPECS:
        rows = list(model.objects.filter(version_id=version_id, subdivision__in=branches).order_by('pk'))
        variants = {}
        for row in rows:
            key = tuple(getattr(row, name) for name in keys)
            variants.setdefault(key, set()).add(tuple(getattr(row, name) for name in fields))
        counts.append({'group': LABELS[model], 'source': sum(row.subdivision_id in ids for row in rows),
                       'target': sum(row.subdivision_id == target.pk for row in rows),
                       'conflicts': sum(len(values) > 1 for values in variants.values())})
        state.append([(row.pk, row.subdivision_id, *[getattr(row, name) for name in (*keys, *fields)]) for row in rows])
    groups = list(PortalReportGroup.objects.filter(subdivisions__in=sources).distinct().order_by('pk').values_list('pk', flat=True))
    profiles = list(MotivationReportAccess.objects.filter(subdivision__in=sources).order_by('pk').values_list('pk', 'subdivision_id'))
    directors = [(row.pk, list(row.directors.order_by('pk').values_list('pk', flat=True))) for row in sources]
    aliases = list(Subdivision.all_objects.filter(merged_into=target).order_by('pk').values_list('pk', 'subdivision_key', 'merge_policy'))
    state.extend([groups, profiles, directors, aliases])
    fingerprint = hashlib.sha256(json.dumps(state, default=str, ensure_ascii=False).encode()).hexdigest()
    return {'fingerprint': fingerprint, 'access_groups': len(groups), 'personal_profiles': len(profiles),
            'source': sources[0], 'sources': sources, 'target': target, 'groups': counts,
            'conflicts': sum(row['conflicts'] for row in counts), 'directors': sum(len(items) for _, items in directors),
            'plan_lines': SalesPlanLine.objects.filter(subdivision__in=[row.subdivision_key for row in sources]).count(),
            'existing_policies': aliases}


def preview(source_id, target_id, version_id):
    return preview_many([source_id], target_id, version_id)


def apply_many(source_ids, target_id, version_id, policy, expected_fingerprint=None, priority_source_id=None):
    if policy not in ('target', 'source'):
        raise ValidationError('Выберите приоритет коэффициентов при совпадении.')
    with ExitStack() as stack:
        for pk in SalesPlanScenario.objects.filter(is_deleted=False).order_by('pk').values_list('pk', flat=True):
            stack.enter_context(scenario_operation(pk))
        with transaction.atomic():
            version = lock_editable(int(version_id))
            ids = sorted(set(int(value) for value in source_ids))
            list(Subdivision.all_objects.select_for_update().filter(pk__in=[*ids, target_id]).order_by('pk'))
            info = preview_many(ids, target_id, version.pk)
            if expected_fingerprint is not None and expected_fingerprint != info['fingerprint']:
                raise ValidationError('Данные изменились после предварительного просмотра. Проверьте объединение заново.')
            sources, target = info['sources'], info['target']
            priority = target
            if policy == 'source':
                priority_id = int(priority_source_id) if priority_source_id else None
                if priority_id is None and len(sources) == 1:
                    priority_id = sources[0].pk
                priority = next((row for row in sources if row.pk == priority_id), None)
                if priority is None:
                    raise ValidationError('Выберите присоединяемое подразделение с приоритетом коэффициентов.')
            # Priority, then the target, then remaining sources in the displayed alphabetical order.
            ordered = [priority, *([target] if priority.pk != target.pk else []), *[row for row in sources if row.pk != priority.pk]]
            for model, keys, fields in SPECS:
                chosen = {}
                for branch in ordered:
                    for row in model.objects.filter(version=version, subdivision=branch).order_by('pk'):
                        identity = tuple(getattr(row, name) for name in keys)
                        chosen.setdefault(identity, row)
                for identity, row in chosen.items():
                    model.objects.update_or_create(version=version, subdivision=target,
                        **dict(zip(keys, identity)), defaults={field: getattr(row, field) for field in fields})
                model.objects.filter(version=version, subdivision__in=sources).delete()
            # A consolidated target may accept more branches later. Keep aliases flat and
            # one explicit historical source preference for the whole resulting group.
            Subdivision.all_objects.filter(merged_into=target).update(merge_policy='target')
            for source in sources:
                for employee in source.directors.all():
                    employee.subdivisions.add(target)
                    employee.subdivisions.remove(source)
                for group in PortalReportGroup.objects.filter(subdivisions=source):
                    group.subdivisions.add(target)
                    group.subdivisions.remove(source)
                MotivationReportAccess.objects.filter(subdivision=source).update(subdivision=target)
                source.merged_into = target
                source.merge_policy = 'source' if source.pk == priority.pk else 'target'
                source.merged_at = timezone.now()
                source.save(update_fields=['merged_into', 'merge_policy', 'merged_at'])
            names = [row.subdivision_key for row in [target, *sources]]
            names += [name for _, name, _ in info['existing_policies']]
            affected = SalesPlanScenario.objects.filter(lines__subdivision__in=names).distinct()
            mutable = [row.pk for row in affected if not row.motivationapproval_set.exists()]
            SalesPlanLine.objects.filter(scenario_id__in=mutable).update(policies_usd=None, sales_usd=None, directors_usd=None, total_usd=None, calculation={})
            SalesPlanScenario.objects.filter(pk__in=mutable).update(calculated_at=None, summary_signature='')
            # Existing mappings must also be re-exported when the group priority changes.
            for old_log in SubdivisionMergeLog.objects.filter(target=target):
                old_log.details = {**old_log.details, 'sql_synced': False}
                old_log.save(update_fields=['details'])
            logs = []
            for source in sources:
                logs.append(SubdivisionMergeLog.objects.create(source=source, target=target, version=version,
                    policy=source.merge_policy, details={'groups': info['groups'], 'access_groups_moved': info['access_groups'],
                    'personal_profiles_moved': info['personal_profiles'], 'plans_to_recalculate': len(mutable), 'sql_synced': False,
                    'batch_sources': [row.pk for row in sources], 'priority_subdivision': priority.subdivision_key,
                    'previous_policies': info['existing_policies']}))
            transaction.on_commit(cache.clear)
            return logs


def apply(source_id, target_id, version_id, policy, expected_fingerprint=None):
    return apply_many([source_id], target_id, version_id, policy, expected_fingerprint)[0]
