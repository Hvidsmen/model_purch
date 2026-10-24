from decimal import Decimal, ROUND_HALF_UP
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import models, transaction
from base.access import can_access
from ..models import (GlobalCoeffVersion, GlobalCoeff, SubdivisionCoeff,
    GlobalDirectorCoeff, SubdivisionDirectorCoeff, TypeCoeff, SegmentCoeff,
    CoefficientGroupCopyLog, touch_version)
from .coefficient_groups import GROUP_NAMES


@transaction.atomic
def copy_group(user, version_id, revision, source, target, subdivision=None, employee=None):
    version = GlobalCoeffVersion.objects.select_for_update().get(pk=version_id)
    admin = can_access(user, 'motivation_admin')
    if not can_access(user, 'motivation') or (not admin and not version.is_editable):
        raise PermissionDenied
    if str(version.revision) != str(revision):
        raise ValidationError('Версия изменена. Обновите страницу и повторите копирование.')
    if source not in GROUP_NAMES or target not in GROUP_NAMES or source == target:
        raise ValidationError('Выберите разные группы источника и назначения.')
    if subdivision and 'directors' in (source, target) and (not employee or not employee.subdivisions.filter(pk=subdivision.pk).exists()):
        raise ValidationError('Выберите директора, привязанного к филиалу.')
    core = SubdivisionCoeff if subdivision else GlobalCoeff
    director = SubdivisionDirectorCoeff if subdivision else GlobalDirectorCoeff
    scope = {'subdivision': subdivision} if subdivision else {}
    director_scope = dict(scope, employee=employee) if subdivision else {}
    segments = {}
    for segment in SegmentCoeff.objects.all():
        label = segment.segment_name.upper().replace('К', 'K').replace('О', 'O')
        if label in {f'{prefix}{i}' for prefix in ('K', 'O') for i in range(5)}:
            segments[segment.pk] = int(label[-1])
    types = {}
    for group in ('policies', 'sales'):
        matches = list(TypeCoeff.objects.filter(type_coeff_name__iexact=GROUP_NAMES[group]))
        if len(matches) != 1 and group in (source, target):
            raise ValidationError('Не найдена однозначная группа коэффициентов.')
        if matches:
            types[group] = matches[0]
    source_cells, templates = {}, {}
    if source == 'directors':
        for row in director.objects.filter(version=version, **director_scope):
            source_cells[row.goods_id] = {i: getattr(row, f'k{i}') for i in range(5)}
    else:
        for row in core.objects.filter(version=version, type_coeff=types[source], **scope):
            if row.segment_id in segments:
                source_cells.setdefault(row.goods_id, {})[segments[row.segment_id]] = Decimal(str(row.motivation_coeff))
                templates[(row.goods_id, row.segment_id)] = row
    if not source_cells:
        raise ValidationError('В выбранной группе нет сохранённых коэффициентов.')
    if any(set(cells) != set(range(5)) for cells in source_cells.values()):
        raise ValidationError('В источнике не заполнены все K0–K4. Копирование отменено.')
    changes = []
    # This operation alone bypasses draft-only save guards after authorization
    # and revision checks. Values and audit entries commit in one transaction.
    if target == 'directors':
        targets = {row.goods_id: row for row in director.objects.filter(version=version, **director_scope)}
        for goods_id, cells in source_cells.items():
            # FloatField can return 0.0015999999999999999. Director fields
            # store six decimal places; normalize before Decimal validation.
            if any(not value.is_finite() or abs(value) > 1 for value in cells.values()):
                raise ValidationError('Коэффициенты источника должны быть от -100% до 100%.')
            cells = {index: value.quantize(Decimal('0.000001'), rounding=ROUND_HALF_UP)
                     for index, value in cells.items()}
            row = targets.get(goods_id)
            before = {f'k{i}': str(getattr(row, f'k{i}')) for i in range(5)} if row else None
            row = row or director(version=version, goods_id=goods_id, **director_scope)
            after = {f'k{i}': str(cells[i]) for i in range(5)}
            for i in range(5):
                setattr(row, f'k{i}', cells[i])
            row.full_clean()
            models.Model.save(row)
            changes.append({'goods': goods_id, 'before': before, 'after': after})
    else:
        targets = {(row.goods_id, row.segment_id): row for row in core.objects.filter(version=version, type_coeff=types[target], **scope)}
        if source == 'directors':
            templates = {(row.goods_id, row.segment_id): row for row in core.objects.filter(version=version, **scope)}
        for goods_id, cells in source_cells.items():
            for segment_id, index in segments.items():
                row = targets.get((goods_id, segment_id))
                before = str(row.motivation_coeff) if row else None
                if row is None:
                    template = templates.get((goods_id, segment_id))
                    if template is None:
                        raise ValidationError('Для товара отсутствуют настройки учёта мотивации в целевой группе.')
                    row = core(version=version, goods_id=goods_id, type_coeff=types[target], segment_id=segment_id,
                        manager_coeff=template.manager_coeff, variation_calculate_id=template.variation_calculate_id, **scope)
                row.motivation_coeff = float(cells[index])
                row.full_clean()
                models.Model.save(row)
                changes.append({'goods': goods_id, 'segment': segment_id, 'before': before, 'after': str(cells[index])})
    if not changes:
        raise ValidationError('Нет ячеек для копирования.')
    CoefficientGroupCopyLog.objects.create(version=version, actor=user.get_username(), source_group=source,
        target_group=target, subdivision=subdivision, employee_id_snapshot=employee.pk if employee else None, changes=changes)
    touch_version(version.pk)
    return len(changes)
