"""Branch-scoped review, immutable deltas and approval invalidation."""
from calendar import monthrange
from copy import deepcopy
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import json
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
from django.utils import timezone
from base.access import role_for, ADMIN, FINANCIER
from ..models import Subdivision, FaoReview, FaoDocument, FaoCorrection, FaoWorkflow, PeriodMotivationCoefficient, PremiumDistribution
from .premium_distribution import cash, event


def authorize(user):
    if role_for(user,'motivation') not in (ADMIN,FINANCIER):raise PermissionDenied


def current(branch,year,period):
    return FaoReview.objects.filter(subdivision=branch,year=year,quarter=4 if period==5 else period).first()


def required(year,period):
    activation=FaoWorkflow.objects.filter(pk=1).values_list('activated_on',flat=True).first()
    quarter=4 if period==5 else period
    return bool(activation and date(year,quarter*3,monthrange(year,quarter*3)[1])>=activation)


def vector(year=None):
    result={}
    reviews=FaoReview.objects.filter(state='completed')
    if year is not None:reviews=reviews.filter(year=year)
    for review in reviews.order_by('pk'):
        result[f'{review.subdivision_id}:{review.year}:{review.quarter}']=review.pk
    return result


def ensure_ready(branch,year,period,execution=None):
    review=current(branch,year,period)
    if not review:
        if required(year,period):raise ValidationError('Сначала завершите проверку ОЗС ФАО подразделения.')
        return
    if review.state!='completed':raise ValidationError('ФАО проводит корректировку этого подразделения. Дождитесь её завершения.')
    if execution is None:
        from ..premium_distribution_views import latest_calculation
        from .main_quarter_mode import enabled
        from .execution_tracking import latest_for_report
        variant='main' if enabled() or period in (4,5) else 'all_2026'
        execution=latest_for_report(latest_calculation(variant),variant=variant,persist=True)
    key=f'{branch.pk}:{year}:{review.quarter}'
    if not execution or execution.state!='completed' or execution.parameters.get('fact_year')!=year or execution.parameters.get('fao_reviews',{}).get(key)!=review.pk:
        raise ValidationError('После проверки ФАО требуется новый завершённый расчёт. Коэффициенты пока не подтверждены повторно.')
    coverage=execution.parameters.get('calculation_subdivisions')
    if coverage!='all' and (not isinstance(coverage,list) or branch.subdivision_key not in coverage):
        raise ValidationError('Расчёт не охватывает проверяемое подразделение. Запустите расчёт с нужным охватом.')
    if period!=5 and period not in execution.parameters.get('premium_amount_quarters',[]):
        raise ValidationError('В расчёте отсутствует премия за квартал проверки ФАО.')


def validate_date(value,review):
    # The correction belongs to this review's open accounting quarter.
    if (value.year,(value.month-1)//3+1)!=(review.year,review.quarter):
        raise ValidationError('Дата корректировки должна относиться к открытому кварталу проверки.')
    end=date(value.year,review.quarter*3,monthrange(value.year,review.quarter*3)[1])
    if end<timezone.localdate():raise ValidationError('Дата корректировки находится в закрытом периоде.')
    if PeriodMotivationCoefficient.objects.filter(subdivision=review.subdivision,year=value.year,period=review.quarter).exists():
        # Opening review invalidates the confirmation; history ending in return
        # means the period is explicitly reopened for this correction cycle.
        record=PeriodMotivationCoefficient.objects.get(subdivision=review.subdivision,year=value.year,period=review.quarter)
        if not record.history or record.history[-1].get('action')!='return_coefficients':
            raise ValidationError('Период уже передан на распределение. Сначала откройте коррекцию ФАО.')


def invalidate(user,branch,year,period):
    record=PeriodMotivationCoefficient.objects.select_for_update().filter(subdivision=branch,year=year,period=period).first()
    batches=list(PremiumDistribution.objects.select_for_update().filter(subdivision=branch,year=year,period=period,state__in=['draft','completed']))
    previous=None
    for batch in batches:
        batch.state='archived';batch.revision+=1
        batch.source={**batch.source,'returned_to_coefficients':True,'fao_return':True};batch.save()
        event(batch,user,'fao_return','ФАО открыло корректировку ОЗС');previous=batch
    if record:
        entry=dict(action='return_coefficients',reason='Корректировка ОЗС ФАО',actor=user.pk,at=timezone.now().isoformat(),
            coefficient=str(record.coefficient),kind_coefficients=deepcopy(record.kind_coefficients),personal_coefficients=deepcopy(record.personal_coefficients))
        if previous:entry.update(distribution=previous.pk,distribution_revision=previous.revision)
        record.history=[*record.history,entry];record.revision+=1;record.save(update_fields=['history','revision'])


@transaction.atomic
def begin(user,branch,year,quarter,reason):
    authorize(user)
    if quarter not in (1,2,3,4) or not 2000<=year<=2100:raise ValidationError('Выберите год и квартал.')
    if date(year,quarter*3,monthrange(year,quarter*3)[1])<timezone.localdate():raise ValidationError('Нельзя открыть корректировку в закрытом квартале.')
    today=timezone.localdate()
    if (year,quarter)!=(today.year,(today.month-1)//3+1):raise ValidationError('Коррекция открывается только в текущем открытом квартале.')
    if not str(reason).strip() or len(reason)>2000:raise ValidationError('Укажите причину открытия, до 2000 знаков.')
    Subdivision.all_objects.select_for_update().get(pk=branch.pk)
    if FaoReview.objects.filter(subdivision=branch,state='open').exists():raise ValidationError('Корректировка подразделения уже открыта.')
    review=FaoReview.objects.create(subdivision=branch,year=year,quarter=quarter,reason=reason.strip(),opened_by=user,opened_label=(user.get_full_name() or user.get_username())[:255])
    invalidate(user,branch,year,quarter)
    invalidate(user,branch,year,5)
    return review


def scale_rows(document,delta,correction_date):
    base=cash(document.original_amount)
    if not base:
        if delta:raise ValidationError('Исходная сумма документа равна нулю: пропорции и исходную ставку определить нельзя.')
        return []
    kinds={row['ТипКоэф'] for row in document.rows}
    base_kind='Продажи' if 'Продажи' in kinds else 'Политики' if 'Политики' in kinds else 'Директора'
    native=sum((Decimal(str(row['СуммаОтгрузокUSD'])) for row in document.rows if row['ТипКоэф']==base_kind),Decimal(0))
    if not native:raise ValidationError('Исходные товарные строки имеют нулевой итог. Пропорция не определена.')
    factor=delta/native
    rows=deepcopy(document.rows)
    monetary=('СуммаОтгрузокUSD','СуммаМотивацииUSD','AmountRURMotive','AmountExwAEUSD')
    for row in rows:
        row['ДатаЗакрытияСделки']=correction_date.isoformat()
        row['Год']=correction_date.year;row['Квартал']=(correction_date.month-1)//3+1;row['Месяц']=correction_date.month
        row['СуммаПланUSD']='0'
    # Preserve each kind's proportional total exactly, including signed lines.
    for kind in {row['ТипКоэф'] for row in rows}:
        indices=[i for i,r in enumerate(rows) if r['ТипКоэф']==kind]
        for field in monetary:
            populated=[i for i in indices if document.rows[i].get(field) is not None]
            if not populated:continue
            exact={i:Decimal(str(document.rows[i][field]))*factor for i in populated}
            target=sum(exact.values(),Decimal(0)).quantize(Decimal('.01'),rounding=ROUND_HALF_UP)
            rounded={i:value.quantize(Decimal('.01'),rounding=ROUND_HALF_UP) for i,value in exact.items()}
            # A deterministic residual goes to the largest contributing line.
            largest=max(populated,key=lambda i:abs(exact[i]))
            rounded[largest]+=target-sum(rounded.values(),Decimal(0))
            for i,value in rounded.items():rows[i][field]=str(value)
    return rows


def preview(user,review,document,new_amount,correction_date,excluded=False):
    authorize(user)
    if review.state!='open' or document.subdivision_id!=review.subdivision_id:raise ValidationError('Документ недоступен в этой проверке.')
    validate_date(correction_date,review)
    from django.db.models import Q
    previous=document.corrections.filter(Q(review__state='completed')|Q(review=review)).order_by('-pk').first()
    before=cash(previous.new_amount if previous else document.original_amount)
    amount=cash(0 if excluded else new_amount)
    rows=scale_rows(document,amount-before,correction_date)
    return dict(before=before,after=amount,delta=amount-before,premiums=[dict(kind=kind,
        usd=sum((Decimal(str(r.get('СуммаМотивацииUSD') or 0)) for r in rows if r['ТипКоэф']==kind),Decimal(0)),
        rub=sum((Decimal(str(r.get('AmountRURMotive') or 0)) for r in rows if r['ТипКоэф']==kind),Decimal(0)) if all(r.get('AmountRURMotive') is not None for r in rows if r['ТипКоэф']==kind) else None) for kind in sorted({r['ТипКоэф'] for r in rows})])


@transaction.atomic
def adjust(user,review_id,revision,document_id,new_amount,correction_date,excluded=False):
    authorize(user)
    branch_id=FaoReview.objects.values_list('subdivision_id',flat=True).get(pk=review_id)
    Subdivision.all_objects.select_for_update().get(pk=branch_id)
    review=FaoReview.objects.select_for_update().select_related('subdivision').get(pk=review_id)
    if review.state!='open' or review.revision!=revision:raise ValidationError('Проверка изменена или завершена. Обновите страницу.')
    validate_date(correction_date,review)
    document=FaoDocument.objects.get(pk=document_id,subdivision_id=branch_id)
    from django.db.models import Q
    previous=FaoCorrection.objects.filter(document=document).filter(Q(review__state='completed')|Q(review=review)).order_by('-pk').first()
    amount=cash(0 if excluded else new_amount)
    before=cash(previous.new_amount if previous else document.original_amount)
    delta=amount-before
    payload=scale_rows(document,delta,correction_date)
    item=FaoCorrection.objects.create(review=review,document=document,previous_amount=before,new_amount=amount,delta=delta,
        correction_date=correction_date,excluded=excluded,payload=payload,actor=user,actor_label=(user.get_full_name() or user.get_username())[:255])
    # Distinguish correction rows from the immutable original document on lookup.
    # This is part of the initial insert; the append-only model is not updated.
    review.revision+=1;review.save(update_fields=['revision'])
    return item


@transaction.atomic
def finish(user,review_id,revision):
    authorize(user)
    branch_id=FaoReview.objects.values_list('subdivision_id',flat=True).get(pk=review_id)
    Subdivision.all_objects.select_for_update().get(pk=branch_id)
    review=FaoReview.objects.select_for_update().get(pk=review_id)
    if review.state!='open' or review.revision!=revision:raise ValidationError('Проверка уже завершена или изменена.')
    review.state='completed';review.revision+=1;review.completed_by=user;review.completed_label=(user.get_full_name() or user.get_username())[:255];review.completed_at=timezone.now();review.save()
    return review


def recalculate(user,review):
    authorize(user)
    if review.state!='completed':raise ValidationError('Сначала завершите проверку ФАО.')
    from ..models import MotivationCalculationSchedule
    from .calculation_jobs import start_job
    from .main_quarter_mode import enabled
    variant='main' if enabled() or review.quarter==4 else 'all_2026'
    from .calculation_variants import main_runs
    from ..models import MotivationExecution
    runs=main_runs() if variant=='main' else MotivationExecution.objects.filter(parameters__variant=variant)
    last=runs.filter(state='completed',parameters__fact_year=review.year).order_by('-started_at','-pk').first()
    job=MotivationCalculationSchedule.objects.filter(fact_year=review.year,variant=variant,key=last.job_key).first() if last else None
    if not job:raise ValidationError('Нет настроенного задания расчёта за этот год. Настройте его в Центре расчётов и повторите запуск.')
    from .execution_tracking import parameters
    coverage=parameters(job).get('calculation_subdivisions')
    if coverage!='all' and (not isinstance(coverage,list) or review.subdivision.subdivision_key not in coverage):raise ValidationError('Задание расчёта не охватывает это подразделение. Настройте полный охват в Центре расчётов.')
    start_job(job)


def readiness(branches,year,period):
    """Readiness for an entire registry without one SQL request per branch."""
    from ..premium_distribution_views import latest_calculation
    from .main_quarter_mode import enabled
    quarter=4 if period==5 else period
    reviews={r.subdivision_id:r for r in FaoReview.objects.filter(year=year,quarter=quarter).order_by('pk')}
    mandatory=required(year,period)
    execution=latest_calculation('main' if enabled() or period in (4,5) else 'all_2026')
    result={}
    for branch in branches:
        review=reviews.get(branch.pk)
        if not review:result[branch.pk]=not mandatory;continue
        parameters=execution.parameters if execution and execution.state=='completed' else {}
        coverage=parameters.get('calculation_subdivisions')
        covered=coverage=='all' or isinstance(coverage,list) and branch.subdivision_key in coverage
        result[branch.pk]=bool(review.state=='completed' and parameters.get('fact_year')==year and covered
            and parameters.get('fao_reviews',{}).get(f'{branch.pk}:{year}:{quarter}')==review.pk
            and (period==5 or period in parameters.get('premium_amount_quarters',[])))
    return result
