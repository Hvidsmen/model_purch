from decimal import Decimal
import hashlib
import unicodedata
from django.db import migrations


def populate(apps, schema_editor):
    Goods = apps.get_model('model_purch', 'PGGoods')
    Group = apps.get_model('model_purch', 'GoodsGroup')
    Freight = apps.get_model('model_purch', 'Freight')
    alias = schema_editor.connection.alias
    groups = {}
    for good in Goods.objects.using(alias).order_by('pk').iterator():
        name = (good.group_goods or '').strip()
        if name:
            normalized = unicodedata.normalize('NFC', name).casefold()
            groups[hashlib.sha256(normalized.encode()).hexdigest()] = (name, good.duty_rate)
    for key, (name, duty) in groups.items():
        Group.objects.using(alias).get_or_create(name_key=key, defaults={'name': name, 'duty_rate': duty})
    freights = {row.scenario_id: row for row in Freight.objects.using(alias).all()}
    for good in Goods.objects.using(alias).iterator(chunk_size=500):
        # Invalid legacy inputs stay untouched and remain subject to preflight checks.
        try:
            exw, volume, container = (Decimal(str(v)) for v in (good.exw_usd, good.volume, good.container_volume))
            if not all(v.is_finite() for v in (exw, volume, container)) or exw < 0 or volume < 0 or container <= 0:
                continue
            freight = freights.get(good.scenario_plan_id)
            ratio = volume / container
            shipping = Decimal(str(freight.price_per_container)) * ratio if freight else Decimal(0)
            cif = exw + shipping
            customs = Decimal(str(freight.customs_rate)) / 100 * cif * (1 + good.duty_rate / 100) if freight else Decimal(0)
            delivery = Decimal(str(freight.warehouse_delivery_cost)) * ratio if freight else Decimal(0)
            good.freight_usd, good.cif_usd = float(shipping), float(cif)
            good.customs_payment_usd, good.warehouse_delivery_usd = float(customs), float(delivery)
            good.ddp_usd = float(cif + customs + delivery)
            if exw > 0:
                good.kddp = float(Decimal(str(good.ddp_usd)) / exw)
            good.save(using=alias, update_fields=['freight_usd', 'cif_usd', 'customs_payment_usd', 'warehouse_delivery_usd', 'ddp_usd', 'kddp'])
        except (ArithmeticError, ValueError, TypeError):
            continue


class Migration(migrations.Migration):
    dependencies = [('model_purch', '0018_pggoods_cif_usd_pggoods_customs_payment_usd_and_more')]
    operations = [migrations.RunPython(populate, migrations.RunPython.noop)]
