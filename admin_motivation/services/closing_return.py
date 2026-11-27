"""Return a frozen allocation to CEO review without reusing director assignments."""
from copy import deepcopy
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone
from base.access import role_for, ADMIN, DIRECTOR
from ..models import Subdivision, PeriodMotivationCoefficient, PremiumDistribution


def pending(record):
    return bool(record and record.history and record.history[-1].get('action') == 'return_coefficients')


def previous_id(record):
    return next((entry.get('distribution') for entry in reversed(record.history) if entry.get('action') == 'return_coefficients'), None) if record else None


@transaction.atomic
def return_to_ceo(user, pk, revision, coefficient_revision, reason):
    if role_for(user, 'motivation') not in (ADMIN, DIRECTOR):
        raise PermissionDenied
    reason = str(reason).strip()
    if not reason or len(reason) > 2000:
        raise ValidationError('Укажите причину возврата (до 2000 знаков).')
    branch_id = PremiumDistribution.objects.values_list('subdivision_id', flat=True).get(pk=pk)
    Subdivision.all_objects.select_for_update().get(pk=branch_id)
    batch = PremiumDistribution.objects.select_for_update().get(pk=pk)
    record = PeriodMotivationCoefficient.objects.select_for_update().filter(subdivision_id=branch_id, year=batch.year, period=batch.period).first()
    if batch.revision != revision or not record or record.revision != coefficient_revision:
        raise ValidationError('Распределение или коэффициенты изменились. Обновите страницу.')
    if batch.state not in ('draft', 'completed') or pending(record):
        raise ValidationError('Распределение уже возвращено или находится в архиве.')
    from .premium_distribution import event
    batch.state = 'archived'
    batch.revision += 1
    batch.source = {**batch.source, 'returned_to_coefficients': True}
    batch.save()
    event(batch, user, 'return_ceo', reason)
    record.history = [*record.history, dict(action='return_coefficients', distribution=batch.pk,
        distribution_revision=batch.revision, coefficient=str(record.coefficient),
        kind_coefficients=deepcopy(record.kind_coefficients), personal_coefficients=deepcopy(record.personal_coefficients),
        reason=reason, actor=user.pk, actor_label=user.get_full_name() or user.get_username(), at=timezone.now().isoformat())]
    record.revision += 1
    record.save(update_fields=['history', 'revision'])
    return batch


def attach_previous(batch, record):
    """Reference amounts only: never copy them to editable amounts or weights."""
    pk = previous_id(record)
    if not pk:
        return
    previous = PremiumDistribution.objects.filter(pk=pk, subdivision=batch.subdivision, year=batch.year,
        period=batch.period, state='archived').first()
    if previous:
        batch.source['previous_distribution'] = dict(id=previous.pk, revision=previous.revision,
            currency=previous.currency, rows=deepcopy(previous.rows), funds=deepcopy(previous.funds))
