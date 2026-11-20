from django.db import migrations,models
class Migration(migrations.Migration):
    dependencies=[('base','0011_salesplanninggoods_manufacturer')]
    operations=[migrations.AddField(model_name=name,name='sales_plan_groups',field=models.JSONField(default=list,blank=True)) for name in ('userprojectaccess','groupprojectaccess')]
