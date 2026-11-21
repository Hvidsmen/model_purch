from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('base', '0012_planner_sales_plan_groups')]
    operations = [migrations.AddField(model_name='splitscenario', name='archived_at', field=models.DateTimeField(blank=True, null=True, db_index=True))]
