"""Review before approval; keep immutable versions and replacement history."""
import hashlib
import json
from django.core import signing
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone
from ..models import GlobalCoeffVersion, GlobalCoeff, SubdivisionCoeff, SalesPlanScenario, MotivationApproval, Goods, Subdivision, TypeCoeff, SegmentCoeff
from .comparison import compare_plan, proposed_changes

SALT = 'motivation-approval-v1'


def state_digest(version, plan):
    versions = list(GlobalCoeffVersion.objects.order_by('pk').values('pk', 'effective_from', 'status', 'revision', 'replaced_by_id', 'title'))
    plan = SalesPlanScenario.objects.get(pk=plan.pk) if plan else None
    payload = {'versions': versions, 'version': version.pk, 'plan': plan.pk if plan else None,
               'loaded_at': plan.loaded_at if plan else None, 'source_version': plan.source_version if plan else None,
               'references': {model._meta.label: list(model.objects.order_by('pk').values_list(*fields)) for model, fields in [
                   (Goods, ('pk', 'planning_group_sales', 'group', 'brand')),
                   (Subdivision, ('pk', 'subdivision_key')),
                   (TypeCoeff, ('pk', 'type_coeff_name')), (SegmentCoeff, ('pk', 'segment_name'))]}}
    return hashlib.sha256(json.dumps(payload, default=str, sort_keys=True).encode()).hexdigest()


def affected_versions(version):
    return GlobalCoeffVersion.objects.filter(status='approved').exclude(pk=version.pk).filter(effective_from__gte=version.effective_from).order_by('effective_from', 'pk')


def review_approval(version, plan=None, baseline=None, subdivision=None, changes=None, deleted=None):
    if version.status != 'draft':
        raise ValidationError('Утвердить можно только черновик.')
    normalized, deleted = proposed_changes(version, subdivision, changes, deleted)
    if not (version.coefficients.exists() or version.subdivision_coefficients.exists()):
        raise ValidationError('Нельзя утвердить пустой набор коэффициентов.')
    before_state = state_digest(version, plan)
    summary = {}
    if plan:
        summary['global'] = compare_plan(plan, version, baseline, changes=normalized if not subdivision else {}, deleted=deleted if not subdivision else [])
        if plan.lines.exclude(subdivision='').exists():
            summary['subdivisions'] = compare_plan(plan, version, baseline, changes=normalized, deleted=deleted, all_subdivisions=True, change_subdivision=subdivision)
            if subdivision:
                summary['subdivision_name'] = subdivision.subdivision_key
    replaced = [{'id': v.pk, 'title': str(v), 'status': v.get_status_display()} for v in affected_versions(version)]
    if before_state != state_digest(version, plan):
        raise ValidationError('Данные изменились во время сравнения. Повторите просмотр.')
    payload = {'state': before_state, 'version': version.pk, 'plan': plan.pk if plan else None,
               'baseline': baseline.pk if baseline else None, 'subdivision': subdivision.pk if subdivision else None,
               'changes': normalized, 'deleted': deleted, 'summary': summary}
    return {'token': signing.dumps(payload, salt=SALT, compress=True), 'replaced': replaced, 'summary': summary,
            'date': version.effective_from.isoformat(), 'unsaved': bool(normalized or deleted),
            'baseline_replaced': bool(baseline and version.effective_from <= baseline.effective_from)}


@transaction.atomic
def approve_review(token, confirm_overwrite=False):
    from ..models import Subdivision
    try:
        payload = signing.loads(token, salt=SALT, max_age=600)
    except signing.BadSignature:
        raise ValidationError('Подтверждение устарело или повреждено. Повторите просмотр утверждения.')
    # Lock the same version rows that draft creation and coefficient mutations lock.
    list(GlobalCoeffVersion.objects.select_for_update().order_by('pk'))
    version = GlobalCoeffVersion.objects.get(pk=payload['version'])
    plan = SalesPlanScenario.objects.select_for_update().get(pk=payload['plan']) if payload['plan'] else None
    if version.status != 'draft' or state_digest(version, plan) != payload['state']:
        raise ValidationError('Версии, коэффициенты или план изменились после просмотра. Повторите сравнение.')
    affected = list(affected_versions(version))
    if (affected or payload.get('baseline') and GlobalCoeffVersion.objects.get(pk=payload['baseline']).effective_from >= version.effective_from) and not confirm_overwrite:
        raise ValidationError('Подтвердите замену версий с даты новой версии включительно.')
    sub = Subdivision.objects.get(pk=payload['subdivision']) if payload['subdivision'] else None
    changes, deleted = proposed_changes(version, sub, payload['changes'], payload['deleted'])
    model = SubdivisionCoeff if sub else GlobalCoeff
    for pk, value in changes.items():
        row = model.objects.get(pk=pk)
        row.motivation_coeff = float(value)
        row.save()
    if deleted:
        version.coefficients.filter(goods_id__in=deleted).delete()
    if affected:
        models.QuerySet.update(GlobalCoeffVersion.objects.filter(pk__in=[v.pk for v in affected]), status='superseded', replaced_by=version)
    models.QuerySet.update(GlobalCoeffVersion.objects.filter(pk=version.pk), status='approved', approved_at=timezone.now(), revision=models.F('revision') + 1)
    MotivationApproval.objects.create(version=version, plan=plan, baseline_id=payload['baseline'],
                                     replaced_ids=[v.pk for v in affected], summary=payload['summary'])
    version.refresh_from_db()
    return version
