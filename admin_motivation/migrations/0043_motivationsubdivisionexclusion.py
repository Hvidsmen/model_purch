from django.db import migrations, models


def seed(apps, schema_editor):
    model = apps.get_model('admin_motivation', 'MotivationSubdivisionExclusion')
    for name in ('E-Com','Retail','Брендинг','Прочее','СНГ','Д-Маврикий','Буфер','Вентиляция','ДПП'):
        model.objects.using(schema_editor.connection.alias).get_or_create(subdivision_name=name)


class Migration(migrations.Migration):
    dependencies = [('admin_motivation', '0042_salesplanline_source_subdivisions')]
    operations = [
        migrations.CreateModel(name='MotivationSubdivisionExclusion', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('subdivision_name', models.CharField(max_length=255, unique=True, verbose_name='SubdivisionName')),
            ('enabled', models.BooleanField(default=True, verbose_name='Исключать из мотивации')),
            ('comment', models.CharField(blank=True, max_length=500, verbose_name='Комментарий')),
        ], options={'ordering': ('subdivision_name',), 'verbose_name': 'исключение подразделения из мотивации', 'verbose_name_plural': 'исключения подразделений из мотивации'}),
        migrations.RunPython(seed, migrations.RunPython.noop),
    ]
