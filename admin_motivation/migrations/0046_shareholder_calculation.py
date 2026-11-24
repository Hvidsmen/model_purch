from decimal import Decimal
import uuid
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies=[('admin_motivation','0045_motivationchannelexclusion_and_more'),migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations=[
        migrations.CreateModel(name='ShareholderCalculation',fields=[
            ('id',models.UUIDField(primary_key=True,default=uuid.uuid4,editable=False,serialize=False)),
            ('name',models.CharField(max_length=160)),('as_of',models.DateField()),
            ('scenario',models.CharField(max_length=12,choices=[('combined','Москва + ТТ объединённо'),('separate','Москва и ТТ раздельно')])),
            ('quarters',models.JSONField(default=list)),('threshold_percent',models.DecimalField(max_digits=7,decimal_places=4,default=Decimal('1'))),
            ('configuration',models.JSONField(default=dict)),('state',models.CharField(max_length=16,default='queued')),
            ('stage',models.CharField(max_length=200,default='Ожидает запуска')),('error',models.TextField(blank=True)),('metadata',models.JSONField(default=dict)),
            ('created_at',models.DateTimeField(auto_now_add=True)),('started_at',models.DateTimeField(null=True)),('completed_at',models.DateTimeField(null=True)),
            ('created_by',models.ForeignKey(to=settings.AUTH_USER_MODEL,on_delete=django.db.models.deletion.PROTECT)),
            ('source_job',models.ForeignKey(to='admin_motivation.motivationcalculationschedule',on_delete=django.db.models.deletion.PROTECT)),
        ],options={'ordering':['-created_at']}),
        migrations.CreateModel(name='ShareholderSnapshot',fields=[
            ('id',models.BigAutoField(auto_created=True,primary_key=True,serialize=False,verbose_name='ID')),
            ('mode',models.CharField(max_length=12)),('dataset',models.CharField(max_length=20)),('sequence',models.PositiveIntegerField()),('payload',models.JSONField(default=list)),
            ('calculation',models.ForeignKey(to='admin_motivation.shareholdercalculation',related_name='chunks',on_delete=django.db.models.deletion.CASCADE)),
        ],options={'ordering':['sequence'],'constraints':[models.UniqueConstraint(fields=('calculation','mode','dataset','sequence'),name='share_snapshot_chunk_unique')]}),
    ]
