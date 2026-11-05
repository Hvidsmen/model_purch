"""Record the actual export and parameters used by SQL Agent, including scheduled runs."""
import json
from datetime import timezone
from django.core.exceptions import ValidationError
from django.utils import timezone as django_timezone
from ..conns import connect_database
from ..models import MotivationExecution

DDL = """
IF OBJECT_ID(N'motivation.PortalMotivationExport',N'U') IS NULL
 CREATE TABLE motivation.PortalMotivationExport (Id int NOT NULL PRIMARY KEY, Snapshot_ nvarchar(max) NOT NULL);
IF OBJECT_ID(N'motivation.PortalMotivationRun',N'U') IS NULL
 CREATE TABLE motivation.PortalMotivationRun (
 RunId uniqueidentifier NOT NULL PRIMARY KEY, JobKey uniqueidentifier NOT NULL,
 StartedAt datetime2 NOT NULL, CompletedAt datetime2 NULL, State_ nvarchar(20) NOT NULL,
 Parameters_ nvarchar(max) NOT NULL, ExportSnapshot nvarchar(max) NULL);
"""


def parameters(job):
    from .pilot_calculation import PERIODS
    from .calculation_variants import variant_of, ALL_2026
    variant = variant_of(job)
    from .transition_2026 import RULE_VERSION
    transition = job.fact_year == 2026
    periods = list('123') if variant == 'separate_2026' else list('12345') if variant == ALL_2026 else list(PERIODS)
    return {'fact_year': job.fact_year, 'plan_version': job.plan_version,
            'quarter_share': str(job.quarter_share), 'year_share': str(job.year_share),
            'payout_threshold': str(job.payout_threshold), 'premium_periods': periods,
            'quarter_catchup': False,
            'premium_amount_quarters': [int(code) for code in periods if code != '5'],
            'variant': variant, 'transition_rule': RULE_VERSION if transition else None,
            'q4_eligibility_quarters': [4] if transition else [1,2,3,4],
            'annual_amount_quarters': [] if variant == 'separate_2026' else [4] if transition or variant == 'main' else [1,2,3,4],
            'calculation_subdivisions': ['Д-Москва', 'ТЕРМОТРЕЙД']}



def tracked_statement(job, statement):
    data = json.dumps(parameters(job),ensure_ascii=False).replace("'","''")
    key = str(job.key)  # UUID model field, never user-provided SQL text.
    return f"""DECLARE @portalRun uniqueidentifier=NEWID();
INSERT INTO motivation.PortalMotivationRun (RunId,JobKey,StartedAt,State_,Parameters_,ExportSnapshot)
 SELECT @portalRun,'{key}',SYSUTCDATETIME(),N'running',N'{data}',
 (SELECT Snapshot_ FROM motivation.PortalMotivationExport WHERE Id=1);
BEGIN TRY
 {statement}
 UPDATE motivation.PortalMotivationRun SET State_=N'completed',CompletedAt=SYSUTCDATETIME() WHERE RunId=@portalRun;
END TRY
BEGIN CATCH
 IF XACT_STATE() <> 0 ROLLBACK;
 UPDATE motivation.PortalMotivationRun SET State_=N'failed',CompletedAt=SYSUTCDATETIME() WHERE RunId=@portalRun;
 EXEC sys.sp_releaseapplock @Resource=N'PortalMotivationCalculation', @LockOwner=N'Session';
 THROW;
END CATCH;"""


def record_export(cursor, snapshot):
    cursor.execute(DDL)
    cursor.execute('DELETE FROM motivation.PortalMotivationExport WHERE Id=1; INSERT INTO motivation.PortalMotivationExport (Id,Snapshot_) VALUES (1,?);',json.dumps(snapshot,ensure_ascii=False,default=str))


def observe(job, connector=None, *, persist=True):
    connection = None
    try:
        connection,cursor=(connector or connect_database)('vm-dwh','DataWH')
        connection.timeout=10
        cursor.execute("""IF OBJECT_ID(N'motivation.PortalMotivationRun',N'U') IS NOT NULL
 SELECT TOP (1) RunId,JobKey,StartedAt,CompletedAt,State_,Parameters_,ExportSnapshot
 FROM motivation.PortalMotivationRun WHERE JobKey=? ORDER BY StartedAt DESC;""",str(job.key))
        row=cursor.fetchone()
        return execution_from_row(row, persist=persist)
    except Exception:
        # Old Agent steps have no audit yet. Never infer a completed run from a timer.
        return None
    finally:
        if connection is not None:
            connection.close()


def execution_from_row(row, *, persist=False):
    if not row or len(row)!=7:
        return None
    run,key,started,completed,state,params,snapshot=row
    if state not in {'running','completed','failed'}:
        return None
    aware=lambda value: value.replace(tzinfo=timezone.utc) if value and django_timezone.is_naive(value) else value
    values={'job_key':key,'state':state,'started_at':aware(started),'completed_at':aware(completed),
            'parameters':json.loads(params),'export_snapshot':json.loads(snapshot) if snapshot else {}}
    if not persist:
        execution=MotivationExecution(run_id=run,**values)
        execution.pk=MotivationExecution.objects.filter(run_id=run).values_list('pk',flat=True).first()
        return execution
    existing = MotivationExecution.objects.filter(run_id=run).first()
    if existing and all(str(getattr(existing, field)).casefold() == str(value).casefold() if field == 'job_key' else getattr(existing, field) == value for field, value in values.items()):
        return existing
    execution,_=MotivationExecution.objects.update_or_create(run_id=run,defaults=values)
    return execution


def observe_latest(variant='main', connector=None, *, persist=False):
    """Read the newest actual SQL Agent run, including scheduled executions."""
    if variant not in ('main', 'all_2026', 'separate_2026'):
        raise ValidationError('Неизвестный вариант расчёта.')
    connection = None
    try:
        connection, cursor = (connector or connect_database)('vm-dwh', 'DataWH')
        connection.timeout = 10
        cursor.execute("""IF OBJECT_ID(N'motivation.PortalMotivationRun',N'U') IS NOT NULL
 SELECT TOP (1) RunId,JobKey,StartedAt,CompletedAt,State_,Parameters_,ExportSnapshot
 FROM motivation.PortalMotivationRun
 WHERE COALESCE(JSON_VALUE(Parameters_,'$.variant'),N'main')=? ORDER BY StartedAt DESC;""", variant)
        return execution_from_row(cursor.fetchone(), persist=persist)
    except Exception:
        # Legacy installations may not have an audit yet. The shared-lock
        # current_run guard still verifies any local fallback before reading.
        return None
    finally:
        if connection is not None:
            connection.close()


def latest_for_report(fallback=None, *, variant='main', persist=False):
    latest = observe_latest(variant, persist=persist)
    if latest is None:
        return fallback
    if latest.state == 'running':
        raise ValidationError('Выполняется новый расчёт. Дождитесь завершения и обновите отчёт.')
    if latest.state == 'failed':
        raise ValidationError('Последний запуск расчёта завершился ошибкой. Проверьте журнал в «Центре расчётов».')
    return latest
