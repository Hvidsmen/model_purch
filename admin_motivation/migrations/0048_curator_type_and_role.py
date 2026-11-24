from django.db import migrations


def seed(apps,schema_editor):
    apps.get_model('admin_motivation','TypeCoeff').objects.get_or_create(type_coeff_name='Кураторы')
    apps.get_model('auth','Group').objects.get_or_create(name='Куратор')


class Migration(migrations.Migration):
    dependencies=[('admin_motivation','0047_curatoremployee_curatorcoefficient')]
    operations=[migrations.RunPython(seed,migrations.RunPython.noop)]
