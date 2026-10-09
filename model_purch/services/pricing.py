from django.db import transaction
"""One source of truth for unit prices; percentages are stored as 0..100."""
from decimal import Decimal, localcontext
import math
from django.core.exceptions import ValidationError
from ..goods_identity import planning_group_key

CALCULATED_FIELDS = ('freight_usd', 'cif_usd', 'customs_payment_usd', 'warehouse_delivery_usd', 'foreign_delivery_usd', 'nr_customs_vat_usd', 'ddp_usd', 'kddp')
INPUT_FIELDS = ('purch', 'planning_sales', 'group_goods', 'brand', 'kind_purch', 'volume',
                'container_volume', 'duty_rate', 'exw_usd', 'stock_cnt_day')


def number(value, label, positive=False, maximum=None):
    try:
        value = Decimal(str(value))
        if not value.is_finite() or value < 0 or (positive and value == 0) or (maximum is not None and value > maximum):
            raise ValueError
        return value
    except Exception as error:
        raise ValidationError(f'{label}: некорректное значение.') from error


def apply_price(good, freight=None, is_russian=False, supply_type=None):
    with localcontext() as context:
        context.prec = 36
        exw = number(good.exw_usd, 'EXW')
        volume = number(good.volume, 'Объём товара')
        container = number(good.container_volume, 'Объём контейнера', positive=True)
        duty = number(good.duty_rate, 'Пошлина', maximum=100) / 100
        ratio = volume / container
        shipping = number(freight.price_per_container, 'Фрахт') * ratio if freight else Decimal(0)
        cif = exw + shipping
        customs = number(freight.customs_rate, 'Таможенная ставка', maximum=100) / 100 * cif * (1 + duty) if freight else Decimal(0)
        delivery = number(freight.warehouse_delivery_cost, 'Доставка до склада') * ratio if freight else Decimal(0)
        foreign_delivery = number(freight.foreign_delivery_cost, 'Загран доставка') * ratio if freight else Decimal(0)
        vat = number(freight.nr_customs_vat_rate, 'НР_Таможенный НДС', maximum=100) / 100 * customs if freight and supply_type == 'p2' else Decimal(0)
        ddp = exw if is_russian or supply_type == 'russian' else cif + customs + delivery
        for name, value in zip(CALCULATED_FIELDS[:-1], (shipping, cif, customs, delivery, foreign_delivery, vat, ddp)):
            converted = float(value)
            if not math.isfinite(converted):
                raise ValidationError('Расчётная стоимость слишком велика.')
            setattr(good, name, converted)
        if exw > 0:
            good.kddp = float(ddp / exw)
    return good


def calculate_instance(good):
    from ..models import Freight, Purch
    freight = Freight.objects.filter(scenario_id=good.scenario_plan_id).first() if good.scenario_plan_id else None
    supplier = Purch.objects.filter(name_key=planning_group_key(good.purch or '')).first()
    return apply_price(good, freight, supply_type=supplier.supply_type if supplier else None)


def reprice_goods(scenario=None, strict=True, goods=None):
    from ..models import Freight, Purch, PGGoods
    if goods is None:
        goods = PGGoods.objects.all()
    if scenario is not None:
        goods = goods.filter(scenario_plan=scenario)
    freights = {row.scenario_id: row for row in Freight.objects.all()}
    suppliers = dict(Purch.objects.values_list('name_key', 'supply_type'))
    batch = []
    invalid = []
    for good in goods.iterator(chunk_size=500):
        try:
            apply_price(good, freights.get(good.scenario_plan_id), supply_type=suppliers.get(planning_group_key(good.purch or '')))
        except ValidationError:
            if strict:
                raise
            invalid.append(good.planning_group)
            continue
        batch.append(good)
        if len(batch) == 500:
            PGGoods.objects.bulk_update(batch, CALCULATED_FIELDS, batch_size=100)
            batch = []
    if batch:
        PGGoods.objects.bulk_update(batch, CALCULATED_FIELDS, batch_size=100)

    return invalid


@transaction.atomic
def apply_group_duty(group_id):
    """Explicitly replace overrides for this group across every scenario."""
    from ..models import GoodsGroup, PGGoods
    group = GoodsGroup.objects.select_for_update().get(pk=group_id)
    ids = [pk for pk, name in PGGoods.objects.values_list('pk', 'group_goods').iterator(chunk_size=500)
           if planning_group_key(name or '') == group.name_key]
    goods = PGGoods.objects.filter(pk__in=ids)
    count = goods.update(duty_rate=group.duty_rate)
    reprice_goods(goods=goods)
    return count
