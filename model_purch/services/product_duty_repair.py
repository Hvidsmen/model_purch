"""Addressed duty repair from an audit: no backups and no group-default writes."""
import csv
import hashlib
import json
import math
from decimal import Decimal, InvalidOperation
from django.core.exceptions import ValidationError
from django.db import transaction
from ..models import PGGoods, ScenarioModel, Freight, Purch
from ..goods_identity import planning_group_key
from .pricing import apply_price, CALCULATED_FIELDS

# These are the known percentage-point values in the reviewed audit.
KNOWN_RATES = {Decimal('5'), Decimal('6.5'), Decimal('9'), Decimal('10')}
COLUMNS = ('Тип','ID товара','ID сценария','Группа','Товар','Сохранённый коэффициент','Проверка')
PRICE_INPUTS = ('duty_rate','exw_usd','volume','container_volume','purch')
FREIGHT_INPUTS = ('price_per_container','customs_rate','warehouse_delivery_cost','foreign_delivery_cost','nr_customs_vat_rate')


def read_targets(path):
    targets=[]; seen=set(); archived=0; other=0
    with open(path,encoding='utf-8-sig',newline='') as source:
        reader=csv.DictReader(source,delimiter=';')
        if not reader.fieldnames or not set(COLUMNS).issubset(reader.fieldnames):
            raise ValidationError('Это не отчёт проверки пошлин: отсутствуют обязательные колонки.')
        for number,row in enumerate(reader,2):
            if row['Тип']!='PRODUCT':
                continue
            try:
                rate=Decimal(row['Сохранённый коэффициент'])
                if not rate.is_finite() or rate<0:
                    raise ValueError
            except (InvalidOperation,ValueError,TypeError):
                raise ValidationError(f'Строка {number}: некорректная исходная ставка.')
            if 'АРХИВ' in row['Проверка'].upper():
                archived+=1; continue
            if rate<=1:
                other+=1; continue
            if rate not in KNOWN_RATES:
                raise ValidationError(f'Строка {number}: ставка {rate} не относится к проверенным значениям 5, 6.5, 9, 10.')
            try:
                pk=int(row['ID товара']); scenario=int(row['ID сценария'])
                if pk<=0 or scenario<=0 or pk in seen:
                    raise ValueError
            except (ValueError,TypeError):
                raise ValidationError(f'Строка {number}: некорректный или повторный ID товара/сценария.')
            seen.add(pk)
            targets.append(dict(id=pk,scenario_id=scenario,group=row['Группа'],product=row['Товар'],old_rate=str(rate),new_rate=str(rate/100)))
    if not targets:
        raise ValidationError('В отчёте нет подходящих действующих товаров для адресного исправления.')
    if len(targets)>10000:
        raise ValidationError('Слишком много адресных исправлений в одном отчёте.')
    return sorted(targets,key=lambda r:r['id']),archived,other


@transaction.atomic
def repair_targets(targets, *, apply=False, expected_preview=None):
    if apply and not expected_preview:
        raise ValidationError('Сначала выполните предварительный пересчёт и передайте его preview_hash через --expected-preview.')
    scenario_ids=sorted({row['scenario_id'] for row in targets})
    scenarios={s.pk:s for s in ScenarioModel.all_objects.select_for_update().filter(pk__in=scenario_ids).order_by('pk')}
    if len(scenarios)!=len(scenario_ids) or any(s.archived_at is not None for s in scenarios.values()):
        raise ValidationError('Сценарий отсутствует или уже перенесён в архив. Обновите отчёт.')
    goods={g.pk:g for g in PGGoods.objects.select_for_update().filter(pk__in=[r['id'] for r in targets]).order_by('pk')}
    if len(goods)!=len(targets):
        raise ValidationError('Товар из отчёта удалён. Обновите отчёт.')
    freights={f.scenario_id:f for f in Freight.objects.select_for_update().filter(scenario_id__in=scenario_ids).order_by('pk')}
    if set(freights)!=set(scenario_ids):
        raise ValidationError('В одном из сценариев нет параметров логистики. Пересчёт отменён.')
    supplier_keys={planning_group_key(g.purch or '') for g in goods.values()}
    suppliers={p.name_key:p for p in Purch.objects.select_for_update().filter(name_key__in=supplier_keys).order_by('pk')}
    changes=[]; updates=[]; fingerprints=[]; correct=[]
    fields=('duty_rate',*CALCULATED_FIELDS)
    for target in targets:
        good=goods[target['id']]
        if (good.scenario_plan_id,good.group_goods,good.planning_group)!=(target['scenario_id'],target['group'],target['product']):
            raise ValidationError(f"Товар #{good.pk}: сценарий, группа или наименование не совпадают с отчётом.")
        old=Decimal(target['old_rate']); new=Decimal(target['new_rate']); current=Decimal(str(good.duty_rate))
        if current not in (old,new):
            raise ValidationError(f'Товар #{good.pk}: ставка уже изменена на {current}. Обновите отчёт, автоматическая замена отменена.')
        freight=freights[good.scenario_plan_id]
        supplier=suppliers.get(planning_group_key(good.purch or ''))
        before={field:getattr(good,field) for field in fields}
        fingerprints.append(dict(target=target,inputs={key:getattr(good,key) for key in PRICE_INPUTS},
            stored=before,freight={key:getattr(freight,key) for key in FREIGHT_INPUTS},
            supply_type=supplier.supply_type if supplier else None))
        good.duty_rate=float(new)
        apply_price(good,freight,supply_type=supplier.supply_type if supplier else None)
        # Already corrected rates remain unchanged; stale stored costs can still be recomputed.
        stale=any(not math.isclose(float(before[key]),float(getattr(good,key)),rel_tol=1e-12,abs_tol=1e-9) for key in CALCULATED_FIELDS)
        if current==new and not stale:
            correct.append(good.pk); continue
        changes.append(dict(id=good.pk,scenario_id=good.scenario_plan_id,product=good.planning_group,
            group=good.group_goods,old_rate=str(current),new_rate=str(new),
            ddp_before=before['ddp_usd'],ddp_after=good.ddp_usd,kddp_after=good.kddp))
        updates.append(good)
    try:
        encoded=json.dumps(fingerprints,ensure_ascii=False,sort_keys=True,allow_nan=False,default=str).encode('utf-8')
    except ValueError:
        raise ValidationError('В сохранённых параметрах обнаружены некорректные числа. Исправление отменено.')
    digest=hashlib.sha256(encoded).hexdigest()
    if apply:
        if expected_preview!=digest:
            raise ValidationError('Данные или параметры расчёта изменились после просмотра. Повторите предварительный пересчёт.')
        if updates:
            PGGoods.objects.bulk_update(updates,fields,batch_size=100)
    return dict(preview_hash=digest,applied=apply,selected=len(targets),changed=len(changes),
                already_correct=correct,rows=changes,backups_created=False)
