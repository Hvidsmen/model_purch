from django.db import migrations


def exclude_dpp(apps, schema_editor):
    model = apps.get_model('admin_motivation', 'MotivationSubdivisionExclusion')
    rows = model.objects.using(schema_editor.connection.alias)
    matching = [pk for pk, name in rows.values_list('pk', 'subdivision_name')
                if name.strip().casefold() == 'дпп']
    if matching:
        rows.filter(pk__in=matching).update(enabled=True)
    else:
        rows.create(subdivision_name='ДПП', enabled=True)


class Migration(migrations.Migration):
    dependencies = [('admin_motivation', '0043_motivationsubdivisionexclusion')]
    operations = [migrations.RunPython(exclude_dpp, migrations.RunPython.noop)]
