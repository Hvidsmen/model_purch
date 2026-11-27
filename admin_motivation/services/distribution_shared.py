"""One remaining balance per payment, shared across all three motivation kinds."""
from collections import defaultdict
from copy import deepcopy
from decimal import Decimal, ROUND_CEILING
import uuid
from django.core.exceptions import ValidationError
from django.utils import timezone

POLICY='shared-payments-v1'
STAGES={'sales':'Продажи','policies':'Политики','directors':'Директора'}


def enabled(source):
    return (source.get('workflow') or {}).get('rebalance_policy')==POLICY


def minimum(row):
    from .premium_distribution import cash
    original=cash(row['original'])
    if original<0:
        return original
    if row['kind'] in ('Политики','Директора') and row.get('author'):
        return (original*Decimal('.75')).quantize(Decimal('.01'),rounding=ROUND_CEILING)
    return Decimal(0)


def pools(funds,rows):
    from .premium_distribution import cash,PAYMENTS
    budgets=defaultdict(lambda:Decimal(0));allocated=defaultdict(lambda:Decimal(0))
    for fund in funds:
        budgets[fund['payment']]+=cash(fund['amount'])
    for row in rows:
        if row['payment'] not in budgets:
            raise ValidationError('Строка относится к отсутствующему типу выплаты.')
        allocated[row['payment']]+=cash(row['amount'])
    return [dict(kind='Общий фонд подразделения',payment=payment,label=PAYMENTS[payment],amount=str(amount),
                 allocated=allocated[payment],remaining=amount-allocated[payment]) for payment,amount in budgets.items()]


def validate(funds,rows,*,complete=False):
    from .premium_distribution import cash
    # Validate the whole person/payment, not only the edited tab or form submission.
    persons=defaultdict(lambda:[Decimal(0),Decimal(0)])
    for row in rows:
        amount=cash(row['amount']);original=cash(row['original'])
        if original<0 and amount!=original:
            raise ValidationError('Отрицательные исходные начисления сохраняются без изменения.')
        if original>=0 and amount<0:
            raise ValidationError('Премия не может быть отрицательной.')
        if row['kind'] in ('Политики','Директора') and row.get('author') and original>=0:
            key=(row['kind'],row['payment'],' '.join(row['author'].split()).casefold())
            persons[key][0]+=original;persons[key][1]+=amount
    for (kind,payment,author),(original,amount) in persons.items():
        floor=(original*Decimal('.75')).quantize(Decimal('.01'),rounding=ROUND_CEILING)
        if amount<floor:
            raise ValidationError(f'{kind}: {author} — уменьшение не более 25% исходной премии. Минимум {floor}.')
    balances=pools(funds,rows)
    if any(p['remaining']<0 for p in balances):
        raise ValidationError('Недостаточно общего остатка этой выплаты. Годовая премия и доплаты не смешиваются.')
    if complete and any(p['remaining']!=0 for p in balances):
        raise ValidationError('Распределите общий остаток каждой выплаты до нуля.')
    if complete and any(cash(r['amount']) and not r.get('author') for r in rows):
        raise ValidationError('Назначьте все суммы сотрудникам.')
    return balances


def update(batch,user,changes,action,addition,comment):
    from .premium_distribution import cash,eligible_employees,event
    if batch.state!='draft':
        raise ValidationError('Распределение завершено. Изменения закрыты.')
    workflow=deepcopy(batch.source['workflow']);stage=workflow['stage']
    kind=STAGES.get(stage)
    navigation={'go_sales':'sales','go_policies':'policies','go_directors':'directors'}
    if not kind or action not in ('save','complete',*navigation):
        raise ValidationError('Неизвестное действие распределения.')
    rows=deepcopy(batch.rows)
    if set(changes)!={row['id'] for row in rows}:
        raise ValidationError('Состав строк изменился. Обновите страницу.')
    for row in rows:
        if row['kind']!=kind:
            continue
        change=changes[row['id']]
        note=str(change.get('comment','')).strip()
        if len(note)>2000:
            raise ValidationError('Комментарий слишком длинный.')
        row.update(amount=str(cash(change['amount'])),comment=note)
    addition=addition or {}
    if addition.get('employee') or addition.get('create') or addition.get('director'):
        payment=addition.get('payment')
        if not any(f['kind']==kind and f['payment']==payment for f in batch.funds):
            raise ValidationError('Выберите тип выплаты текущей вкладки.')
        if addition.get('kind')!=kind:
            raise ValidationError('Добавление доступно только в текущем виде мотивации.')
        if kind=='Продажи' and not addition.get('director'):
            if addition.get('create'):
                from ..models import SalesEmployee
                name=' '.join(str(addition.get('full_name','')).split())
                if not name or len(name)>255:
                    raise ValidationError('Укажите ФИО сотрудника до 255 знаков.')
                if any(' '.join(v.split()).casefold()==name.casefold() for v in SalesEmployee.objects.filter(subdivision=batch.subdivision).values_list('full_name',flat=True)):
                    raise ValidationError('Сотрудник уже есть в подразделении. Выберите его из справочника.')
                employee=SalesEmployee(full_name=name,personnel_number='AUTO-'+uuid.uuid4().hex,subdivision=batch.subdivision)
                from django.db import IntegrityError,transaction
                try:
                    with transaction.atomic():
                        employee.full_clean();employee.save()
                except IntegrityError:
                    raise ValidationError('Сотрудник уже создан. Обновите страницу и выберите его из справочника.')
            else:
                employee=eligible_employees(batch).filter(pk=addition.get('employee')).first()
            field='employee_id'
        elif kind=='Директора' and addition.get('director') and not addition.get('employee') and not addition.get('create'):
            employee=batch.subdivision.directors.filter(pk=addition['director'],is_archived=False).first()
            field='director_id'
        else:
            raise ValidationError('Выберите сотрудника из справочника текущего подразделения.')
        if not employee:
            raise ValidationError('Сотрудник не относится к этому подразделению или недоступен в периоде.')
        if any(r['kind']==kind and r['payment']==payment and ' '.join(r['author'].split()).casefold()==' '.join(employee.full_name.split()).casefold() for r in rows):
            raise ValidationError('Сотрудник уже есть в этой выплате.')
        rows.append(dict(id=uuid.uuid4().hex,author=employee.full_name,kind=kind,payment=payment,original='0.00',amount='0.00',comment='',**{field:employee.pk}))
    validate(batch.funds,rows,complete=action=='complete')
    if action in navigation:
        workflow['stage']=navigation[action]
    if action=='complete':
        batch.state='completed';batch.completed_by=user;batch.completed_at=timezone.now()
        workflow.update(stage='completed',rebalance_by=user.pk,rebalance_at=timezone.now().isoformat())
    batch.rows=rows;batch.source={**batch.source,'workflow':workflow};batch.revision+=1;batch.save()
    event(batch,user,action,comment)
    return batch


def upgrade(user,pk,revision):
    """Explicit opt-in for an existing draft; keep its edits and frozen originals."""
    from django.db import transaction
    from ..models import PremiumDistribution,Subdivision
    from .premium_distribution import authorize,read_source,event
    with transaction.atomic():
        branch_id=PremiumDistribution.objects.values_list('subdivision_id',flat=True).get(pk=pk)
        Subdivision.all_objects.select_for_update().get(pk=branch_id)
        batch=PremiumDistribution.objects.select_for_update().select_related('subdivision','execution').get(pk=pk)
        authorize(user,batch.subdivision,write=True)
        if batch.revision!=revision:
            raise ValidationError('Распределение изменилось. Обновите страницу.')
        if batch.state!='draft' or not batch.source.get('workflow') or enabled(batch.source):
            raise ValidationError('Обновление доступно только для старого черновика с этапами.')
        funds=deepcopy(batch.funds);rows=deepcopy(batch.rows)
        if not any(f['kind']=='Директора' for f in funds):
            new_funds,new_rows=read_source(batch.execution,batch.subdivision,batch.period,batch.currency,include_directors=True)
            funds.extend(f for f in new_funds if f['kind']=='Директора')
            rows.extend(r for r in new_rows if r['kind']=='Директора')
        validate(funds,rows)
        batch.funds=funds;batch.rows=rows
        workflow={**batch.source['workflow'],'rebalance_policy':POLICY,'stage':'sales'}
        batch.source={**batch.source,'workflow':workflow};batch.revision+=1;batch.save()
        event(batch,user,'upgrade_shared','Общий остаток, лимит 25%, добавлены директора. Существующие исходные суммы и назначения сохранены.')
        return batch
