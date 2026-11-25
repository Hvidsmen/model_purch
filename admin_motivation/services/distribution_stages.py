"""Ordered sales-weight allocation, then politician rebalance, then completion."""
from copy import deepcopy
from decimal import Decimal, InvalidOperation, ROUND_FLOOR
import uuid
from django.core.exceptions import ValidationError
from django.utils import timezone


def initialize(batch):
    from .premium_distribution import eligible_employees
    originals=batch.rows
    sales=[]
    employees=list(eligible_employees(batch))
    for fund in batch.funds:
        if fund['kind']!='Продажи':continue
        for employee in employees:
            previous=next((row for row in originals if row['kind']=='Продажи' and row['payment']==fund['payment'] and row['author'].casefold()==employee.full_name.casefold()),{})
            sales.append({'id':uuid.uuid4().hex,'employee_id':employee.pk,'author':employee.full_name,'kind':'Продажи',
                'payment':fund['payment'],'original':previous.get('original','0.00'),'amount':'0.00','weight':'0','comment':''})
    batch.rows=[*sales,*[row for row in originals if row['kind']=='Политики']]


def weight(value):
    try:
        result=Decimal(str(value).replace(',','.'))
        if not result.is_finite() or not 0<=result<=Decimal('1000000000') or result!=result.quantize(Decimal('.000001')):raise ValueError
        return result
    except (InvalidOperation,ValueError,TypeError):raise ValidationError('Вес сотрудника: неотрицательное число, до шести знаков после запятой.')


def allocate(funds,rows,*,require_complete=False):
    from .premium_distribution import cash
    for fund in funds:
        if fund['kind']!='Продажи':continue
        members=[r for r in rows if r['kind']=='Продажи' and r['payment']==fund['payment']]
        weights=[weight(r.get('weight',0)) for r in members]
        denominator=sum(weights,Decimal(0));amount=cash(fund['amount'])
        if not denominator:
            for row in members:row['amount']='0.00'
            if require_complete and amount:raise ValidationError('Добавьте сотрудников и задайте ненулевые веса для каждого фонда продаж.')
            continue
        sign=-1 if amount<0 else 1
        cents=int(abs(amount)*100)
        exact=[Decimal(cents)*w/denominator for w in weights]
        rounded=[int(v.to_integral_value(rounding=ROUND_FLOOR)) for v in exact]
        # Largest remainder preserves the fund exactly and assigns nothing to zero weights.
        order=sorted(range(len(members)),key=lambda i:(-(exact[i]-rounded[i]),members[i]['id']))
        for index in order[:cents-sum(rounded)]:rounded[index]+=1
        for row,value in zip(members,rounded):row['amount']=str(sign*Decimal(value)/100)


def update(batch,user,changes,action,addition,comment):
    from .premium_distribution import cash,balances,eligible_employees,event
    if batch.state!='draft':raise ValidationError('Распределение завершено. Изменения закрыты.')
    workflow=deepcopy(batch.source['workflow']);stage=workflow['stage']
    if addition and (addition.get('employee') or addition.get('create')) and (stage!='sales' or addition.get('kind')!='Продажи'):
        raise ValidationError('Добавление сотрудников доступно на этапе продаж.')
    if action not in ('save','sales_complete','return_sales','complete'):raise ValidationError('Неизвестный переход этапа.')
    if action in ('return_sales','complete') and stage!='policies' or action=='sales_complete' and stage!='sales':
        raise ValidationError('Сначала завершите предыдущий этап.')
    rows=deepcopy(batch.rows)
    if set(changes)!={r['id'] for r in rows}:raise ValidationError('Состав строк изменился. Обновите страницу.')
    for row in rows:
        change=changes[row['id']]
        if stage=='sales' and row['kind']=='Продажи':
            row['weight']=str(weight(change.get('weight',row.get('weight','0'))))
        elif stage=='policies' and row['kind']=='Политики':
            amount=cash(change['amount']);note=str(change.get('comment','')).strip()
            if len(note)>2000:raise ValidationError('Комментарий слишком длинный.')
            fund=next(f for f in batch.funds if f['kind']==row['kind'] and f['payment']==row['payment'])
            if cash(fund['amount'])>=0 and amount<0:raise ValidationError('Сумма не может быть отрицательной.')
            row.update(amount=str(amount),comment=note)
    if addition and (addition.get('employee') or addition.get('create')):
        if stage!='sales' or addition.get('kind')!='Продажи':raise ValidationError('Добавление сотрудников доступно на этапе продаж.')
        payment=addition.get('payment')
        if not any(f['kind']=='Продажи' and f['payment']==payment for f in batch.funds):raise ValidationError('Выберите фонд продаж.')
        if addition.get('create'):
            from ..models import SalesEmployee
            from django.db import IntegrityError,transaction
            name=' '.join(str(addition.get('full_name','')).split())
            personnel=str(addition.get('personnel_number','')).strip()
            if not name or len(name)>255:raise ValidationError('Укажите ФИО сотрудника (до 255 знаков).')
            if len(personnel)>64:raise ValidationError('Табельный номер: до 64 знаков.')
            names=SalesEmployee.objects.filter(subdivision=batch.subdivision).values_list('full_name',flat=True)
            if any(' '.join(value.split()).casefold()==name.casefold() for value in names):
                raise ValidationError('Такой сотрудник уже есть в подразделении. Выберите его из справочника; архивного сотрудника сначала восстановите.')
            employee=SalesEmployee(full_name=name,personnel_number=personnel,subdivision=batch.subdivision)
            try:
                with transaction.atomic():
                    employee.full_clean();employee.save()
            except IntegrityError:raise ValidationError('Сотрудник уже создан. Обновите страницу и выберите его из списка.')
        else:employee=eligible_employees(batch).filter(pk=addition['employee']).first()
        if not employee:raise ValidationError('Выберите сотрудника подразделения, работавшего в этом периоде.')
        if any(r.get('employee_id')==employee.pk and r['kind']=='Продажи' and r['payment']==payment for r in rows):raise ValidationError('Сотрудник уже есть в этом фонде.')
        rows.append({'id':uuid.uuid4().hex,'employee_id':employee.pk,'author':employee.full_name,'kind':'Продажи','payment':payment,'original':'0.00','amount':'0.00','weight':str(weight(addition.get('weight','0'))),'comment':''})
    if stage=='sales':
        if rows!=batch.rows:
            workflow.pop('sales_by',None);workflow.pop('sales_at',None)
        allocate(batch.funds,rows,require_complete=action=='sales_complete')
        if action=='sales_complete':workflow.update(stage='policies',sales_by=user.pk,sales_at=timezone.now().isoformat())
    if action=='return_sales':workflow['stage']='sales'
    if action=='complete':
        if not workflow.get('sales_at'):raise ValidationError('Сначала зафиксируйте премии продаж.')
        if any(f['remaining']!=0 for f in balances(batch.funds,rows)):raise ValidationError('Каждый фонд должен быть распределён полностью.')
        if any(cash(r['amount']) and not r['author'] for r in rows):raise ValidationError('Назначьте все суммы сотрудникам.')
        batch.state='completed';batch.completed_at=timezone.now();batch.completed_by=user
        workflow.update(stage='completed',policies_by=user.pk,policies_at=timezone.now().isoformat())
    batch.rows=rows;batch.source={**batch.source,'workflow':workflow};batch.revision+=1;batch.save();event(batch,user,action,comment)
    return batch


from django.db import transaction

@transaction.atomic
def begin_legacy(user, pk, revision, coefficient, reason):
    """Explicitly reset a legacy draft into ordered stages; keep earlier events."""
    from ..models import PremiumDistribution,Subdivision,PeriodMotivationCoefficient
    from .closing_coefficient import confirm
    from .premium_distribution import authorize,cash,event
    from base.access import role_for,ADMIN,DIRECTOR
    from django.core.exceptions import PermissionDenied
    if role_for(user,'motivation') not in (ADMIN,DIRECTOR):raise PermissionDenied
    branch_id=PremiumDistribution.objects.values_list('subdivision_id',flat=True).get(pk=pk)
    Subdivision.all_objects.select_for_update().get(pk=branch_id)
    batch=PremiumDistribution.objects.select_for_update().select_related('subdivision').get(pk=pk)
    authorize(user,batch.subdivision,write=True)
    if batch.revision!=revision:raise ValidationError('Распределение изменилось. Обновите страницу.')
    if batch.state!='draft' or batch.source.get('workflow'):raise ValidationError('Перевод доступен только для старого незавершённого распределения.')
    if not reason.strip() or len(reason)>2000:raise ValidationError('Укажите причину перехода на этапы (до 2000 знаков).')
    current=PeriodMotivationCoefficient.objects.filter(subdivision=batch.subdivision,year=batch.year,period=batch.period).first()
    factor=confirm(user,batch.subdivision,batch.year,batch.period,coefficient,current.revision if current else 0,legacy_distribution=batch)
    before={'funds':deepcopy(batch.funds),'rows':deepcopy(batch.rows)}
    from decimal import ROUND_HALF_UP
    for row in batch.rows:
        row['original']=str((cash(row['original'])*factor.coefficient).quantize(Decimal('.01'),rounding=ROUND_HALF_UP))
        row['amount']=row['original'];row['comment']=''
    for fund in batch.funds:
        fund['amount']=str(sum((cash(row['original']) for row in batch.rows if (row['kind'],row['payment'])==(fund['kind'],fund['payment'])),Decimal(0)))
    batch.source={**batch.source,'before_stages':before,'workflow':{'stage':'sales','coefficient':str(factor.coefficient),
        'coefficient_revision':factor.revision,'confirmed_by':user.pk,'confirmed_at':factor.confirmed_at.isoformat()}}
    initialize(batch);batch.revision+=1;batch.save();event(batch,user,'begin_stages',reason)
    return batch
