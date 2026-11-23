"""Data-only trial helpers. Reports contain counts/hashes, never records or credentials."""
from datetime import date,datetime,time,timezone,timedelta
from decimal import Decimal
from hashlib import sha256
import json
import uuid
from django.apps import apps
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import connections,models,transaction
from django.db.migrations.executor import MigrationExecutor
from .sqlserver_trial_config import trial_name

MARKER='PortalSqliteTrialRun'
EXCLUDED={'sessions.session'}


def require_trial():
    if not getattr(settings,'PORTAL_SQLSERVER_TRIAL',False):
        raise ValidationError('Команда доступна только с portal.settings_sqlserver_trial.')
    connection=connections['default']
    name=trial_name(connection.settings_dict['NAME'])
    if connection.vendor!='microsoft':raise ValidationError('Назначение должно быть SQL Server.')
    expected=getattr(settings,'PORTAL_TRIAL_RUN_ID','')
    if not expected:raise ValidationError('Не задан PORTAL_TRIAL_RUN_ID тестового переноса.')
    with connection.cursor() as cursor:
        cursor.execute("SELECT DB_NAME(), CAST(value AS nvarchar(100)) FROM sys.extended_properties WHERE class=0 AND name=%s",[MARKER])
        row=cursor.fetchone()
    if not row or row[0].casefold()!=name.casefold() or row[1]!=expected:
        raise ValidationError('База не помечена как назначение этого тестового переноса.')
    return connection


def set_phase(connection,phase):
    with connection.cursor() as cursor:
        cursor.execute("EXEC sys.sp_updateextendedproperty @name=N'PortalSqliteTrialPhase', @value=%s",[phase])


def clear_migration_seeds():
    connection=require_trial()
    with connection.cursor() as cursor:
        cursor.execute("SELECT CAST(value AS nvarchar(100)) FROM sys.extended_properties WHERE class=0 AND name=N'PortalSqliteTrialPhase'")
        row=cursor.fetchone()
    if not row or row[0]!='new':raise ValidationError('Очистка seed-данных разрешена только один раз в новой тестовой базе.')
    tables=[name for name in connection.introspection.table_names() if name!='django_migrations']
    with transaction.atomic(using='default'):
        with connection.constraint_checks_disabled():
            with connection.cursor() as cursor:
                for name in tables:cursor.execute('DELETE FROM '+connection.ops.quote_name(name))
        connection.check_constraints()
        set_phase(connection,'loading')


def reseed_identities():
    connection=require_trial()
    with connection.cursor() as cursor:
        for model in apps.get_models(include_auto_created=True):
            if not model._meta.managed or model._meta.proxy:continue
            if not isinstance(model._meta.pk,(models.AutoField,models.BigAutoField,models.SmallAutoField)):continue
            table=connection.ops.quote_name(model._meta.db_table);pk=connection.ops.quote_name(model._meta.pk.column)
            cursor.execute('SELECT COALESCE(MAX('+pk+'),0) FROM '+table)
            maximum=int(cursor.fetchone()[0])
            # Table originates in model metadata; numeric maximum is validated as int.
            literal=model._meta.db_table.replace("'","''")
            cursor.execute("DBCC CHECKIDENT (N'"+literal+"', RESEED, "+str(maximum)+") WITH NO_INFOMSGS")


def canonical(value):
    if isinstance(value,dict):return {str(key):canonical(item) for key,item in value.items()}
    if isinstance(value,(list,tuple)):return [canonical(item) for item in value]
    if isinstance(value,datetime):
        if value.tzinfo is not None:value=value.astimezone(timezone.utc)
        return {'datetime':value.isoformat()}
    if isinstance(value,(date,time)):return {type(value).__name__:value.isoformat()}
    if isinstance(value,Decimal):
        if not value.is_finite():raise ValueError('Нефинитное Decimal в исходных данных.')
        if not value:return {'decimal':'0'}
        text=format(value,'f')
        return {'decimal':text.rstrip('0').rstrip('.') if '.' in text else text}
    if isinstance(value,timedelta):return {'duration_us':value.days*86400000000+value.seconds*1000000+value.microseconds}
    if isinstance(value,uuid.UUID):return str(value)
    if isinstance(value,(bytes,bytearray,memoryview)):return {'hex':bytes(value).hex()}
    if isinstance(value,float) and value==0:return 0.0
    return value


def digest_model(model,alias):
    fields=[field for field in model._meta.concrete_fields if not (model._meta.auto_created and field.primary_key)]
    names=[field.attname for field in fields]
    order=[field.attname for field in fields if field.is_relation] if model._meta.auto_created else [model._meta.pk.attname]
    digest=sha256();count=0
    for row in model._base_manager.using(alias).order_by(*order).values_list(*names).iterator(chunk_size=250):
        data=json.dumps(canonical(row),sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode()
        digest.update(len(data).to_bytes(8,'big'));digest.update(data);count+=1
    return {'count':count,'sha256':digest.hexdigest()}


def verify_data():
    connection=require_trial()
    result={'models':[],'passed':True,'excluded':sorted(EXCLUDED)}
    for model in apps.get_models(include_auto_created=True):
        if not model._meta.managed or model._meta.proxy or model._meta.swapped or model._meta.label_lower in EXCLUDED:continue
        source=digest_model(model,'trial_source');target=digest_model(model,'default')
        same=source==target
        result['models'].append({'model':model._meta.label_lower,'source':source,'target':target,'equal':same})
        result['passed'] &= same
    executor=MigrationExecutor(connection)
    result['pending_migrations']=[[item.app_label,item.name] for item,backwards in executor.migration_plan(executor.loader.graph.leaf_nodes())]
    result['passed'] &= not result['pending_migrations']
    connection.check_constraints()
    return result


def compatibility_probe():
    """Create temporary records, exercise actual SQL semantics, and roll back."""
    require_trial()
    from django.contrib.auth.models import User
    from django.db.models import Count,Q
    from model_purch.models import ModelDDPCalculation,ModelDDPRow
    tests={}
    with transaction.atomic(using='default'):
        user=User.objects.create_user('trial-probe-'+uuid.uuid4().hex)
        batch=ModelDDPCalculation.objects.create(name='Проба JSON / Unicode',as_of=date.today(),created_by=user)
        first=ModelDDPRow.objects.create(calculation=batch,model_ref='probe-a',model_name='Модель А',price=Decimal('12.34567890'),currency='USD',
            source_data={'classification':{'brand':'Марка'}},result={'status':'ready','ddp':{'USD':'12.345678'}})
        second=ModelDDPRow.objects.create(calculation=batch,model_ref='probe-b',model_name='Модель Б',result={})
        tests['identity_after_import']=first.pk!=second.pk and batch.pk is not None
        tests['json_empty']=batch.rows.filter(result={}).count()==1
        tests['json_path']=batch.rows.filter(result__status='ready').count()==1
        tests['json_nested_unicode']=batch.rows.filter(source_data__classification__brand='Марка').count()==1
        stats=batch.rows.aggregate(ready=Count('pk',filter=Q(result__status='ready')))
        tests['json_filtered_aggregate']=stats['ready']==1
        first.overrides={'volume':'0.123456789123'};second.overrides={'volume':'2'}
        ModelDDPRow.objects.bulk_update([first,second],['overrides'],batch_size=2)
        first.refresh_from_db()
        tests['json_bulk_update']=first.overrides=={'volume':'0.123456789123'}
        tests['decimal_precision']=first.price==Decimal('12.34567890')
        list(batch.rows.select_for_update().order_by('pk'))
        tests['row_locks']=True
        tests['json_text_roundtrip']=first.result.get('ddp',{}).get('USD')=='12.345678'
        transaction.set_rollback(True,using='default')
    return {'checks':tests,'passed':all(tests.values())}
