from django.db import migrations,models


class Migration(migrations.Migration):
    dependencies=[('admin_motivation','0053_shareholder_archive')]
    operations=[migrations.AddField(model_name='globalcoeffversion',name='planning_rules',field=models.JSONField(default=list,blank=True,verbose_name='Правила групп планов продаж'))]
