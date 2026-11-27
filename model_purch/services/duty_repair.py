"""Explicit, previewable repair of one known percentage-entry mistake."""
import json
from decimal import Decimal, InvalidOperation
from pathlib import Path
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from ..goods_identity import planning_group_key
from ..models import GoodsGroup, PGGoods, Freight, Purch
from .pricing import apply_price, CALCULATED_FIELDS


@transaction.atomic
def repair(group_name, old_rate, new_percent, *, apply=False, backup_file=None):
    try:
        old = Decimal(str(old_rate)); percent = Decimal(str(new_percent))
        if not old.is_finite() or not percent.is_finite() or not 0<=old<=100 or not 0<=percent<=10000:
            raise ValueError
    except (InvalidOperation, ValueError, TypeError):
        raise ValidationError('Укажите конечные неотрицательные ставки в допустимом диапазоне.')
    group = GoodsGroup.objects.select_for_update().get(name_key=planning_group_key(group_name))
    if Decimal(str(group.duty_rate)) != old:
        raise ValidationError('Ставка группы изменилась или не совпадает с ожидаемой. Повторите проверку.')
    new_rate = percent / 100
    if new_rate == old:
        raise ValidationError('Новая ставка совпадает с текущей.')
    freight = {f.scenario_id:f for f in Freight.objects.all()}
    suppliers = dict(Purch.objects.values_list('name_key','supply_type'))
    fields = ('duty_rate', *CALCULATED_FIELDS)
    changes=[]; objects=[]; preserved=0
    names=[name for name in PGGoods.objects.values_list('group_goods',flat=True).distinct() if planning_group_key(name or '')==group.name_key]
    for good in PGGoods.objects.select_for_update().filter(group_goods__in=names,scenario_plan__archived_at__isnull=True).order_by('pk').iterator(chunk_size=500):
        if Decimal(str(good.duty_rate)) != old:
            preserved += 1
            continue
        before = {field:getattr(good,field) for field in fields}
        good.duty_rate = float(new_rate)
        apply_price(good,freight.get(good.scenario_plan_id),supply_type=suppliers.get(planning_group_key(good.purch or '')))
        changes.append(dict(id=good.pk,scenario_id=good.scenario_plan_id,planning_group=good.planning_group,
                            before=before,after={field:getattr(good,field) for field in fields}))
        objects.append(good)
    preview = dict(at=timezone.now().isoformat(),group_id=group.pk,group=group.name,
                   old_rate=str(old),new_rate=str(new_rate),new_percent=str(percent),
                   goods=changes,preserved_individual=preserved,archive='unchanged',applied=False)
    if apply:
        if not backup_file:
            raise ValidationError('Для исправления укажите файл резервной копии.')
        path=Path(backup_file)
        path.parent.mkdir(parents=True,exist_ok=True)
        # Exclusive creation: never overwrite an earlier repair backup.
        with path.open('x',encoding='utf-8') as output:
            json.dump(preview,output,ensure_ascii=False,indent=2,allow_nan=False)
        group.duty_rate=float(new_rate);group.save(update_fields=['duty_rate'])
        if objects:
            PGGoods.objects.bulk_update(objects,fields,batch_size=100)
        preview['applied']=True
    return preview
