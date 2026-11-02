from django.db import migrations, models


def exempt_existing_scenarios(apps, schema_editor):
    # Include archived scenarios as well: restoring an old plan must keep its policy.
    scenario = apps.get_model('model_purch', 'ScenarioModel')
    scenario._base_manager.using(schema_editor.connection.alias).all().update(check_weight_completeness=False)


class Migration(migrations.Migration):
    dependencies = [('model_purch', '0031_pggoods_weight_kg')]
    operations = [
        migrations.AddField(
            model_name='scenariomodel', name='check_weight_completeness',
            field=models.BooleanField(default=True, editable=False, verbose_name='Проверять заполненность веса'),
        ),
        migrations.RunPython(exempt_existing_scenarios, migrations.RunPython.noop),
    ]
