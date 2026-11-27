"""FAO's final payment confirmation closes only the selected accounting scope."""
from django.core.exceptions import ValidationError
from django.db import transaction
from ..models import FaoPaymentClosure, FaoReview, PremiumDistribution, Subdivision


def closure(branch,year,period):
    # Annual settlement also prevents corrections that would invalidate that year.
    return FaoPaymentClosure.objects.filter(subdivision=branch,year=year,period__in={period,5}).order_by('period').first()


def ensure_open(branch,year,period):
    if closure(branch,year,period):
        raise ValidationError('Период закрыт ФАО: выплаты произведены. Корректировки и повторное распределение запрещены.')


@transaction.atomic
def mark_paid(user,pk,revision):
    from .fao_review import authorize
    from .premium_distribution import event
    authorize(user)
    branch_id=PremiumDistribution.objects.values_list('subdivision_id',flat=True).get(pk=pk)
    Subdivision.all_objects.select_for_update().get(pk=branch_id)
    batch=PremiumDistribution.objects.select_for_update().get(pk=pk)
    ensure_open(batch.subdivision,batch.year,batch.period)
    if batch.state!='completed':raise ValidationError('Сначала завершите распределение премий.')
    if batch.revision!=revision:raise ValidationError('Распределение изменилось. Обновите страницу перед подтверждением выплаты.')
    reviews=FaoReview.objects.filter(subdivision_id=branch_id,year=batch.year,state='open')
    if batch.period!=5:reviews=reviews.filter(quarter=batch.period)
    if reviews.exists():raise ValidationError('Сначала завершите корректировку ФАО и повторное распределение.')
    record=FaoPaymentClosure.objects.create(subdivision=batch.subdivision,year=batch.year,period=batch.period,
        distribution=batch,distribution_revision=batch.revision,actor=user,
        actor_label=(user.get_full_name() or user.get_username())[:255])
    batch.revision+=1;batch.save(update_fields=['revision'])
    event(batch,user,'payments_made','ФАО подтвердило: выплаты произведены, период закрыт.')
    return record
