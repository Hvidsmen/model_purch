"""Frozen submissions, finance sign-off and director approval."""
import hashlib
import json
from decimal import Decimal, InvalidOperation
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.serializers.json import DjangoJSONEncoder
from django.db import models, transaction
from base.access import role_for, ADMIN, MODELER, FINANCIER, DIRECTOR
from ..models import (GlobalCoeffVersion, GlobalCoeff, SubdivisionCoeff, SubdivisionManagerCoeff,
    GlobalDirectorCoeff, SubdivisionDirectorCoeff, DiscountSegment, MotivationReview,
    MotivationReviewEvent, SalesPlanScenario, Goods, TypeCoeff, SegmentCoeff, VariationCalculate,
    Subdivision, DirectorEmployee, KindManagerCoeff)
from .approval import review_approval, approve_review

PENDING = ('finance_review', 'director_review')
COEFFICIENT_MODELS = (GlobalCoeff, SubdivisionCoeff, SubdivisionManagerCoeff,
                      GlobalDirectorCoeff, SubdivisionDirectorCoeff, DiscountSegment)


def label(actor):
    return (actor.get_full_name().strip() or actor.get_username())[:255]


def snapshot(version):
    data = {model._meta.model_name: list(model.objects.filter(version=version).order_by('pk').values()) for model in COEFFICIENT_MODELS}
    from .subdivision_aliases import mapping
    from .subdivision_scope import included
    branches = dict(Subdivision.all_objects.values_list('pk', 'subdivision_key'))
    aliases = mapping()
    for name in ('subdivisioncoeff', 'subdivisionmanagercoeff', 'subdivisiondirectorcoeff'):
        data[name] = [row for row in data[name] if included(branches.get(row['subdivision_id'], ''), aliases)]
    data['version'] = {'title': version.title, 'effective_from': version.effective_from}
    data['references'] = {model._meta.model_name: list(model.objects.order_by('pk').values()) for model in
        (Goods, TypeCoeff, SegmentCoeff, VariationCalculate, Subdivision, DirectorEmployee, KindManagerCoeff)}
    data['bindings'] = list(DirectorEmployee.subdivisions.through.objects.order_by('pk').values())
    return json.loads(json.dumps(data, cls=DjangoJSONEncoder))


def fingerprint(version, plan, data=None):
    digest = hashlib.sha256(json.dumps(data or snapshot(version), sort_keys=True, ensure_ascii=False).encode())
    plan.refresh_from_db()
    digest.update(json.dumps([plan.pk, plan.loaded_at, plan.source_version, plan.is_deleted], default=str).encode())
    # Detect plan edits/reloads even when timestamps or row counts are unchanged.
    for row in plan.lines.order_by('pk').values().iterator(chunk_size=2000):
        digest.update(json.dumps(row, sort_keys=True, default=str).encode())
    # Changes to the approved history change the comparison baseline as well.
    for row in GlobalCoeffVersion.objects.filter(status='approved').order_by('pk').values('pk', 'revision', 'effective_from'):
        digest.update(json.dumps(row, sort_keys=True, default=str).encode())
    return digest.hexdigest()


def cells(data):
    refs = {name: {row['id']: row for row in rows} for name, rows in data['references'].items()}
    result = {}
    titles = {'globalcoeff': 'Глобально', 'subdivisioncoeff': 'Подразделение',
              'subdivisionmanagercoeff': 'Поправка руководителя', 'globaldirectorcoeff': 'Директор · глобально',
              'subdivisiondirectorcoeff': 'Директор', 'discountsegment': 'Сегменты скидок'}
    for name, title in titles.items():
        for row in data.get(name, []):
            good = refs['goods'].get(row.get('goods_id'), {})
            sub = refs['subdivision'].get(row.get('subdivision_id'), {})
            employee = refs['directoremployee'].get(row.get('employee_id'), {})
            kind = refs['typecoeff'].get(row.get('type_coeff_id'), {}).get('type_coeff_name', '')
            segment = refs['segmentcoeff'].get(row.get('segment_id'), {}).get('segment_name', '')
            period = refs['kindmanagercoeff'].get(row.get('kind_id'), {}).get('name', '')
            scope = sub.get('subdivision_key', title)
            product = ' · '.join(str(good.get(field, '')) for field in ('planning_group_sales', 'group', 'brand')) if good else row.get('type_discount', period)
            identity = (name, row.get('goods_id'), row.get('subdivision_id'), row.get('employee_id'), row.get('type_coeff_id'), row.get('segment_id'), row.get('kind_id'), row.get('type_discount'))
            fields = [field for field in row if field not in {'id', 'version_id', 'goods_id', 'subdivision_id', 'employee_id', 'type_coeff_id', 'segment_id', 'kind_id', 'type_discount'}]
            for field in fields:
                value = row[field]
                field_label = {'motivation_coeff': segment, 'manager_coeff': 'Множитель', 'variation_calculate_id': 'Формула', 'coeff': period}.get(field, field.upper())
                if field == 'variation_calculate_id':
                    value = refs['variationcalculate'].get(value, {}).get('variation_name', value)
                elif value is not None and (field == 'motivation_coeff' or field.startswith('k') and len(field) == 2):
                    value = f'{Decimal(str(value)) * 100:f}%'
                result[(*identity, field)] = {'scope': scope, 'product': product, 'kind': kind or title,
                    'employee': employee.get('full_name', ''), 'field': field_label, 'value': value}
    return result


def differences(current, previous):
    after, before = cells(current), cells(previous) if previous else {}
    changes = []
    for key in sorted(set(after) | set(before), key=str):
        a, b = after.get(key), before.get(key)
        if a == b:
            continue
        changes.append({**(a or b), 'before': b['value'] if b else '—', 'after': a['value'] if a else 'Удалено'})
    return changes


def event(review, actor, action, comment=''):
    return MotivationReviewEvent.objects.create(review=review, actor=actor, actor_label=label(actor),
        actor_role=role_for(actor, 'motivation'), action=action, comment=comment.strip())


def submit(actor, version_id, plan_id, revision, comment=''):
    if role_for(actor, 'motivation') not in (ADMIN, MODELER):
        raise PermissionDenied
    version = GlobalCoeffVersion.objects.get(pk=version_id)
    if not version.is_editable or str(version.revision) != str(revision):
        raise ValidationError('Черновик изменился или недоступен. Обновите страницу.')
    plan = SalesPlanScenario.objects.get(pk=plan_id, is_deleted=False)
    if not plan.loaded_at or not plan.lines.exists():
        raise ValidationError('Для проверки выберите загруженный непустой план продаж.')
    data = snapshot(version)
    stamp = fingerprint(version, plan, data)
    baseline = GlobalCoeffVersion.objects.filter(status='approved', effective_from__lte=version.effective_from).order_by('-effective_from', '-pk').first()
    prepared = review_approval(version, plan, baseline)
    for key in ('global', 'subdivisions'):
        current = prepared['summary'].get(key, {}).get('variants', {}).get('current', {})
        if current.get('error'):
            raise ValidationError(f'Не удалось проверить плановую премию: {current["error"]}')
    for name in ('globalcoeff', 'subdivisioncoeff', 'globaldirectorcoeff', 'subdivisiondirectorcoeff'):
        for row in data[name]:
            fields = ['motivation_coeff'] if 'motivation_coeff' in row else [f'k{i}' for i in range(5)]
            for field in fields:
                try:
                    value = Decimal(str(row[field]))
                    valid = value.is_finite() and -1 <= value <= 1
                except (InvalidOperation, TypeError):
                    valid = False
                if not valid:
                    raise ValidationError('В наборе есть некорректный коэффициент. Проверьте значения перед отправкой.')
    for row in version.discount_segments.all():
        row.clean()
    prepared['summary']['fallbacks'] = {key: prepared['summary'].get(key, {}).get('variants', {}).get('current', {}).get('fallbacks', 0) for key in ('global', 'subdivisions')}
    prepared['summary']['checks'] = [
        'План продаж загружен и содержит строки.',
        'Сохранённый набор коэффициентов не пуст; числовые ставки проверены.',
        'Плановая премия рассчитана для глобальных коэффициентов и подразделений.',
        'Пороги заданных сегментов скидок проверены.',
        'Состав набора и план зафиксированы; изменения проверяются перед каждым решением.'
    ]
    changes = differences(data, snapshot(baseline) if baseline else None)
    with transaction.atomic():
        list(GlobalCoeffVersion.objects.select_for_update().order_by('pk'))
        version.refresh_from_db()
        if not version.is_editable or str(version.revision) != str(revision) or fingerprint(version, plan) != stamp:
            raise ValidationError('Данные изменились во время подготовки. Повторите отправку.')
        review = MotivationReview.objects.create(version=version, plan=plan, baseline=baseline,
            submitted_by=actor, actor_label=label(actor), fingerprint=stamp, snapshot=data,
            changes=changes, summary=prepared['summary'], comment=comment.strip())
        models.QuerySet.update(GlobalCoeffVersion.objects.filter(pk=version.pk), status='finance_review', revision=models.F('revision') + 1)
        event(review, actor, 'submitted', comment)
        version.refresh_from_db()
        return review


def active(review):
    return review.version.reviews.order_by('-pk').values_list('pk', flat=True).first() == review.pk and review.version.status in PENDING


def validate_frozen(review):
    if not active(review):
        raise ValidationError('Эта проверка уже завершена или заменена новой отправкой.')
    if review.plan.is_deleted or fingerprint(review.version, review.plan) != review.fingerprint:
        raise ValidationError('План или настройки изменились после отправки. Верните набор на доработку и отправьте на проверку заново.')


@transaction.atomic
def decide(actor, review_id, action, comment='', confirm_overwrite=False):
    role = role_for(actor, 'motivation')
    if role not in (ADMIN, FINANCIER, DIRECTOR):
        raise PermissionDenied
    list(GlobalCoeffVersion.objects.select_for_update().order_by('pk'))
    review = MotivationReview.objects.select_related('version', 'plan', 'baseline').get(pk=review_id)
    if not active(review):
        raise ValidationError('Эта проверка уже завершена. Обновите страницу.')
    stage = review.version.status
    permitted = role == ADMIN or role == FINANCIER and stage == 'finance_review' or role == DIRECTOR and stage == 'director_review'
    if not permitted:
        raise PermissionDenied
    if action == 'return':
        if not comment.strip():
            raise ValidationError('Укажите замечания для доработки.')
        models.QuerySet.update(GlobalCoeffVersion.objects.filter(pk=review.version_id), status='draft', revision=models.F('revision') + 1)
        event(review, actor, 'returned', comment)
        return review
    validate_frozen(review)
    if action == 'agree' and role == FINANCIER and stage == 'finance_review':
        models.QuerySet.update(GlobalCoeffVersion.objects.filter(pk=review.version_id), status='director_review', revision=models.F('revision') + 1)
        event(review, actor, 'agreed', comment)
    elif action == 'approve' and role == DIRECTOR and stage == 'director_review':
        if not review.events.filter(action='agreed', actor_role=FINANCIER).exists():
            raise ValidationError('Сначала требуется согласование финансиста.')
        prepared = review_approval(review.version, review.plan, review.baseline)
        approve_review(prepared['token'], confirm_overwrite, actor=actor, workflow_review=review)
        event(review, actor, 'approved', comment)
    elif action == 'override' and role == ADMIN:
        if not comment.strip():
            raise ValidationError('Укажите причину утверждения без прохождения проверок.')
        prepared = review_approval(review.version, review.plan, review.baseline)
        approve_review(prepared['token'], confirm_overwrite, actor=actor, override_reason=comment, workflow_review=review)
        event(review, actor, 'override', comment)
    else:
        raise PermissionDenied
    return review
