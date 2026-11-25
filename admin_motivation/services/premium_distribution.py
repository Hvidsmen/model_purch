"""Frozen funds, optimistic revisions and auditable director allocation."""
from calendar import monthrange
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import uuid
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone
from base.access import role_for, ADMIN, DIRECTOR, BRANCH_DIRECTOR, FINANCIER
from base.report_access import politician_scope
from ..models import PremiumDistribution, PremiumDistributionEvent

KINDS=('Продажи','Политики')
PAYMENTS={'quarter':'Квартальная премия','annual':'Годовая премия','catchup':'Доплата за кварталы'}


def branches(user, *, write=False):
    role=role_for(user,'motivation')
    if role in (ADMIN,DIRECTOR) or (not write and role==FINANCIER):return None
    if role==BRANCH_DIRECTOR:
        scope=politician_scope(user)
        if scope and scope.get('all_authors'):
            value=scope['subdivision']
            return value if isinstance(value,list) else [value]
    raise PermissionDenied


def authorize(user, subdivision, *, write=False):
    allowed=branches(user,write=write)
    if allowed is not None and subdivision.subdivision_key not in allowed:raise PermissionDenied


def cash(value):
    try:
        number=Decimal(str(value).replace(' ','').replace('\xa0','').replace(',','.'))
        if not number.is_finite() or abs(number)>Decimal('999999999999.99') or number!=number.quantize(Decimal('.01')):raise ValueError
        return number.quantize(Decimal('.01'))
    except (InvalidOperation,ValueError,TypeError):raise ValidationError('Сумма должна быть числом с точностью до двух знаков.')


def balances(funds,rows):
    result=[]
    for fund in funds:
        allocated=sum((cash(row['amount']) for row in rows if (row['kind'],row['payment'])==(fund['kind'],fund['payment'])),Decimal(0))
        result.append({**fund,'label':PAYMENTS[fund['payment']],'allocated':allocated,'remaining':cash(fund['amount'])-allocated})
    return result


def event(batch,user,action,comment=''):
    return PremiumDistributionEvent.objects.create(distribution=batch,revision=batch.revision,action=action,actor=user,
        actor_label=user.get_full_name() or user.get_username(),comment=comment,
        snapshot={'funds':batch.funds,'rows':batch.rows,'state':batch.state,'currency':batch.currency,'source':batch.source})


def read_source(execution,subdivision,period,currency):
    from . import reports_next
    from .reports import load_report
    variant=execution.parameters.get('variant','main')
    if execution.state!='completed' or variant not in ('main','all_2026','separate_2026'):raise ValidationError('Нужен завершённый рабочий расчёт.')
    if variant=='main' and period in (1,2,3) and period not in execution.parameters.get('premium_amount_quarters',[]):raise ValidationError('Нужно заново запустить основной расчёт с I–III кварталами. Текущий сохранённый запуск их ещё не содержит.')
    if variant=='separate_2026' and period not in (1,2,3):raise ValidationError('Раздельный расчёт содержит только I–III кварталы.')
    rows=[]; funds=[]
    # Shared DWH application lock prevents replacing results during the snapshot.
    with reports_next.current_run(execution):
        for kind in KINDS:
            data=load_report('manager-detail',{'TypeCoeff_':kind,'Subdivision':[subdivision.subdivision_key],
                'DealAuthor':[],'gp_sales':[],'GPGoods':[],'Brend':[],'Years':[str(execution.parameters['fact_year'])],'Currency':currency},
                premium_period=[str(period)],summary_only=True,calculation_variant=variant)
            summary=data['period_summary']
            index=next((i for i,col in enumerate(summary['columns']) if reports_next.period_code(col['label'])==str(period)),None)
            if index is None:raise ValidationError('В расчёте отсутствует выбранный период. Снимок не создан.')
            payments=['annual','catchup'] if period==5 else ['quarter']
            for payment in payments:
                subset=[]
                for item in summary['rows']:
                    if item['subdivision'].casefold()!=subdivision.subdivision_key.casefold():raise ValidationError('Расчёт вернул другое подразделение.')
                    value=item.get('remainder') if payment=='catchup' else item['values'][index]
                    if value is None:continue
                    amount=Decimal(str(value)).quantize(Decimal('.01'),rounding=ROUND_HALF_UP)
                    if not amount.is_finite():raise ValidationError('В расчёте некорректная сумма.')
                    author='' if item.get('unallocated') else str(item['author']).strip()
                    subset.append({'id':uuid.uuid4().hex,'author':author,'kind':kind,'payment':payment,
                        'original':str(amount),'amount':str(amount),'comment':''})
                funds.append({'kind':kind,'payment':payment,'amount':str(sum((cash(row['original']) for row in subset),Decimal(0)))})
                rows.extend(subset)
    if not rows:raise ValidationError('В расчёте нет сумм для распределения.')
    return funds,rows


def create(user,execution,subdivision,period,currency):
    authorize(user,subdivision,write=True)
    if currency not in ('USD','RUB') or period not in (1,2,3,4,5):raise ValidationError('Выберите период и валюту.')
    year=int(execution.parameters.get('fact_year',0))
    if not 2000<=year<=2100:raise ValidationError('Некорректный год расчёта.')
    month=12 if period==5 else period*3
    if date(year,month,monthrange(year,month)[1])>=timezone.localdate():raise ValidationError('Период ещё не закрыт.')
    if PremiumDistribution.objects.filter(subdivision=subdivision,year=year,period=period,state__in=['draft','completed']).exists():raise ValidationError('Распределение за этот период уже создано. Откройте его из списка.')
    with transaction.atomic():
        from ..models import PeriodMotivationCoefficient
        type(subdivision).all_objects.select_for_update().get(pk=subdivision.pk)
        coefficient=PeriodMotivationCoefficient.objects.select_for_update().filter(subdivision=subdivision,year=year,period=period).first()
        if not coefficient:raise ValidationError('Этап 1: гендиректор должен подтвердить общий множитель, в том числе 1,0.')
        funds,rows=read_source(execution,subdivision,period,currency)
        batch=PremiumDistribution.objects.create(execution=execution,subdivision=subdivision,year=year,period=period,currency=currency,
            funds=funds,rows=rows,created_by=user,source={'run_id':str(execution.run_id),'parameters':execution.parameters,'export_snapshot':execution.export_snapshot})
        batch.source={**batch.source,'workflow':{'stage':'sales','coefficient':str(coefficient.coefficient),
            'coefficient_revision':coefficient.revision,'confirmed_by':coefficient.confirmed_by_id,
            'confirmed_at':coefficient.confirmed_at.isoformat()}}
        from .distribution_stages import initialize
        initialize(batch);batch.save()
        event(batch,user,'created')
    return batch


@transaction.atomic
def update(user,pk,revision,changes,*,action='save',addition=None,comment=''):
    batch=PremiumDistribution.objects.select_for_update().select_related('subdivision').get(pk=pk)
    authorize(user,batch.subdivision,write=True)
    if revision!=batch.revision:raise ValidationError('Распределение уже изменено. Обновите страницу перед сохранением.')
    if batch.source.get('workflow') and action!='reopen':
        from .distribution_stages import update as staged_update
        return staged_update(batch,user,changes,action,addition,comment)
    if action=='reopen':
        if role_for(user,'motivation') not in (ADMIN,DIRECTOR):raise PermissionDenied
        if batch.state!='completed' or not comment.strip():raise ValidationError('Для повторного открытия укажите причину.')
        batch.state='draft';batch.completed_at=None;batch.completed_by=None
        if batch.source.get('workflow'):
            batch.source={**batch.source,'workflow':{**batch.source['workflow'],'stage':'sales'}}
    else:
        if batch.state!='draft':raise ValidationError('Распределение завершено. Изменения закрыты.')
        if action not in ('save','complete'):raise ValidationError('Неизвестное действие.')
        rows=[]
        if set(changes)!={row['id'] for row in batch.rows}:raise ValidationError('Состав строк изменён. Обновите страницу.')
        for row in batch.rows:
            change=changes[row['id']]
            amount=cash(change['amount']); note=str(change.get('comment','')).strip()
            if len(note)>2000:raise ValidationError('Комментарий слишком длинный.')
            fund=next(f for f in batch.funds if (f['kind'],f['payment'])==(row['kind'],row['payment']))
            if cash(fund['amount'])>=0 and amount<0:raise ValidationError('Распределяемая сумма не может быть отрицательной.')
            rows.append({**row,'amount':str(amount),'comment':note})
        if addition and addition.get('employee'):
            employee=eligible_employees(batch).filter(pk=addition['employee']).first()
            if not employee:raise ValidationError('Выберите сотрудника подразделения, работавшего в этом периоде.')
            author=employee.full_name
            if len(author)>255:raise ValidationError('ФИО слишком длинное.')
            key=(addition.get('kind'),addition.get('payment'))
            if not any((f['kind'],f['payment'])==key for f in batch.funds):raise ValidationError('Такого фонда нет в распределении.')
            if any((r['author'].casefold(),r['kind'],r['payment'])==(author.casefold(),*key) for r in rows):raise ValidationError('Сотрудник уже есть в этом фонде.')
            rows.append({'id':uuid.uuid4().hex,'employee_id':employee.pk,'author':author,'kind':key[0],'payment':key[1],'original':'0.00','amount':'0.00','comment':''})
        if any(f['kind']=='Политики' and cash(f['amount'])>=0 and f['remaining']<0 for f in balances(batch.funds,rows)):
            raise ValidationError('Нельзя распределить больше фонда. Сначала уменьшите другую премию.')
        batch.rows=rows
        if action=='complete':
            if any(b['remaining']!=0 for b in balances(batch.funds,rows)):raise ValidationError('Распределите каждый фонд полностью: остатки должны быть равны нулю.')
            if any(cash(r['amount'])!=0 and not r['author'] for r in rows):raise ValidationError('Нераспределённые суммы нужно назначить сотрудникам.')
            batch.state='completed';batch.completed_at=timezone.now();batch.completed_by=user
    batch.revision+=1;batch.save();event(batch,user,action,comment)
    return batch


def eligible_employees(batch):
    from django.db.models import Q
    from ..models import SalesEmployee
    first=1 if batch.period==5 else (batch.period-1)*3+1
    last=12 if batch.period==5 else batch.period*3
    start=date(batch.year,first,1);end=date(batch.year,last,monthrange(batch.year,last)[1])
    return SalesEmployee.objects.filter(subdivision=batch.subdivision).filter(Q(started_on__isnull=True)|Q(started_on__lte=end)).filter(Q(ended_on__isnull=True)|Q(ended_on__gte=start)).filter(Q(is_archived=False)|Q(ended_on__isnull=False))


@transaction.atomic
def retire(user, pk, revision, action, reason):
    """Remove drafts from work or archive allocations without losing audit history."""
    branch_id=PremiumDistribution.objects.values_list('subdivision_id',flat=True).get(pk=pk)
    from ..models import Subdivision
    Subdivision.all_objects.select_for_update().get(pk=branch_id)
    batch=PremiumDistribution.objects.select_for_update().select_related('subdivision').get(pk=pk)
    authorize(user,batch.subdivision,write=True)
    if action not in ('delete_draft','archive'):raise ValidationError('Неизвестное действие.')
    if action=='archive' and role_for(user,'motivation')!=ADMIN:raise PermissionDenied
    if batch.revision!=revision:raise ValidationError('Распределение изменилось. Обновите страницу.')
    if batch.state not in ('draft','completed'):raise ValidationError('Распределение уже удалено или находится в архиве.')
    if action=='delete_draft' and batch.state!='draft':raise ValidationError('Удалить можно только черновое распределение. Завершённое может архивировать администратор.')
    reason=str(reason).strip()
    if not reason or len(reason)>2000:raise ValidationError('Укажите причину (до 2000 знаков).')
    batch.state='archived' if action=='archive' else 'deleted'
    batch.revision+=1;batch.save();event(batch,user,action,reason)
    return batch
