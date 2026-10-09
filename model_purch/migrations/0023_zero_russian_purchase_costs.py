import unicodedata
from django.db import migrations


def zero_costs(apps, schema_editor):
    alias = schema_editor.connection.alias
    Purch = apps.get_model('model_purch', 'Purch')
    Goods = apps.get_model('model_purch', 'PGGoods')
    def key(value):
        return unicodedata.normalize('NFC', value or '').strip().casefold()
    names = {key(name) for name in Purch.objects.using(alias).filter(supply_type='russian').values_list('name', flat=True)}
    ids = [pk for pk, name in Goods.objects.using(alias).values_list('pk', 'purch').iterator() if key(name) in names]
    for start in range(0, len(ids), 500):
        Goods.objects.using(alias).filter(pk__in=ids[start:start + 500]).update(
            freight_usd=0, cif_usd=0, customs_payment_usd=0,
            warehouse_delivery_usd=0, foreign_delivery_usd=0, nr_customs_vat_usd=0,
        )


class Migration(migrations.Migration):
    dependencies = [('model_purch', '0022_alter_freight_foreign_delivery_cost')]
    operations = [migrations.RunPython(zero_costs, migrations.RunPython.noop)]
