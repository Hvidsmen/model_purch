from django.db import migrations


def add_formula(apps, schema_editor):
    apps.get_model('admin_motivation', 'VariationCalculate').objects.get_or_create(variation_name='Подразделение(Глобально)')


class Migration(migrations.Migration):
    dependencies = [('admin_motivation', '0020_versionreopenlog')]
    operations = [migrations.RunPython(add_formula, migrations.RunPython.noop)]
