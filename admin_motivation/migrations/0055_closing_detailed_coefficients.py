from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('admin_motivation', '0054_planning_group_rules')]
    operations = [
        migrations.AddField(model_name='periodmotivationcoefficient', name='kind_coefficients', field=models.JSONField(default=dict)),
        migrations.AddField(model_name='periodmotivationcoefficient', name='personal_coefficients', field=models.JSONField(default=dict)),
    ]
