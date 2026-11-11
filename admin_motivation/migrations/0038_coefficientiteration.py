from django.conf import settings
import django.db.models.deletion
from django.db import migrations,models


class Migration(migrations.Migration):
    dependencies=[('admin_motivation','0037_directorscenariocomparison'),migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations=[migrations.CreateModel(name='CoefficientIteration',fields=[
        ('id',models.BigAutoField(auto_created=True,primary_key=True,serialize=False,verbose_name='ID')),
        ('payload',models.JSONField()),('mappings',models.JSONField(default=dict)),
        ('confirmed',models.BooleanField(default=False)),('revision',models.PositiveIntegerField(default=1)),
        ('created_at',models.DateTimeField(auto_now_add=True)),('confirmed_at',models.DateTimeField(blank=True,null=True)),
        ('owner',models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,to=settings.AUTH_USER_MODEL)),
    ],options={'ordering':['-pk']})]
