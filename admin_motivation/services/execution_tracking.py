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
    from .motivation_exclusions import rules
    from .pilot_calculation import PERIODS
    from .calculation_variants import variant_of, ALL_2026
    variant = variant_of(job)
    from .transition_2026 import RULE_VERSION
    transition = job.fact_year == 2026
    from .annual_accrual import SNAPSHOT_VERSION
    periods = list('123') if variant == 'separate_2026' else list('12345') if variant == ALL_2026 else list(PERIODS)
    if variant == 'test_2025':
        from .recalculation_2025 import RULE_VERSION
        return {'fact_year': 2025, 'plan_version': job.plan_version,
                'quarter_share': str(job.quarter_share), 'year_share': str(job.year_share),
                'payout_threshold': str(job.payout_threshold), 'premium_periods': list('12345'),
                'quarter_catchup': True, 'premium_amount_quarters': [1,2,3,4],
                'variant': variant, 'recalculation_rule': RULE_VERSION,
                'policy_plan_shares': 'fact_2025_by_subdivision_coefficient_goods',
                'missing_fact_policy_plan_shares': 'goods_without_brand_then_planning_group_then_original_subdivision',
                'no_subdivision_authors': 'unallocated_plan_zero_premium',
                'zero_sum_policy_plan_shares': 'positive_author_net_by_original_subdivision_coefficient_goods',
                'coefficient_sha256': job.recalculation_2025.source_sha256,
                'coefficient_exclusions':list(rules()),
                'calculation_subdivisions': 'all_separate', 'currency': 'USD'}
    return {'fact_year': job.fact_year, 'plan_version': job.plan_version,
            'quarter_share': str(job.quarter_share), 'year_share': str(job.year_share),
            'payout_threshold': str(job.payout_threshold), 'premium_periods': periods,
            'quarter_catchup': False,
            'premium_amount_quarters': [int(code) for code in periods if code != '5'],
            'variant': variant, 'transition_rule': RULE_VERSION if transition else None,
            'q4_eligibility_quarters': [4] if transition else [1,2,3,4],
            'annual_amount_quarters': [] if variant == 'separate_2026' else [4] if transition or variant == 'main' else [1,2,3,4],
            'coefficient_exclusions':list(rules()),
            'calculation_subdivisions': ['Д-Москва', 'ТЕРМОТРЕЙД'],
            'annual_accrual_source': SNAPSHOT_VERSION}



def tracked_statement(job, statement, *, release_lock=True):
    data = json.dumps(parameters(job),ensure_ascii=False).replace("'","''")
    key = str(job.key)  # UUID model field, never user-provided SQL text.
    caption = {'main': 'Основной запуск', 'all_2026': 'Объединённый расчёт',
               'separate_2026': 'Раздельный расчёт', 'test_2025': 'Пересчёт 2025'}[parameters(job)['variant']]
    progress_begin=progress_end=progress_failed=''
    if parameters(job)['variant']=='test_2025':
        from .recalculation_2025_progress import begin_sql,finish_sql
        progress_begin=begin_sql();progress_end=finish_sql();progress_failed=finish_sql(failed=True)
    release = "EXEC sys.sp_releaseapplock @Resource=N'PortalMotivationCalculation', @LockOwner=N'Session';" if release_lock else ''
    from .prepared_sales import RULE as prepared_rule
    prepared_params = f"JSON_MODIFY(JSON_MODIFY(N'{data}','$.prepared_sales_run',(SELECT CONVERT(nvarchar(36),a.RunId) FROM motivation.PortalPreparedSalesActive a JOIN motivation.PortalPreparedSalesRun r ON r.RunId=a.RunId WHERE a.Id=1 AND r.State_=N'completed' AND r.Rule_=N'{prepared_rule}')),'$.prepared_sales_rule',N'{prepared_rule}')"
    return f"""DECLARE @portalRun uniqueidentifier=NEWID(), @portalStarted datetime2=SYSUTCDATETIME();
INSERT INTO motivation.PortalMotivationRun (RunId,JobKey,StartedAt,State_,Parameters_,ExportSnapshot)
 SELECT @portalRun,'{key}',SYSUTCDATETIME(),N'running',{prepared_params},
 (SELECT Snapshot_ FROM motivation.PortalMotivationExport WHERE Id=1);
BEGIN TRY
 {progress_begin}
 {statement}
 PRINT N'{caption}: '+CONVERT(nvarchar(30),DATEDIFF(SECOND,@portalStarted,SYSUTCDATETIME()))+N' сек.';
 {progress_end}
 UPDATE motivation.PortalMotivationRun SET State_=N'completed',CompletedAt=SYSUTCDATETIME() WHERE RunId=@portalRun;
END TRY
BEGIN CATCH
 IF XACT_STATE() <> 0 ROLLBACK;
 {progress_failed}
 UPDATE motivation.PortalMotivationRun SET State_=N'failed',CompletedAt=SYSUTCDATETIME() WHERE RunId=@portalRun;
 {release}
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
    if variant not in ('main', 'all_2026', 'separate_2026', 'test_2025'):
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



def running_state(job, execution, connector=None, *, check_agent=False):
    """Derive a cancelled run without rewriting the warehouse audit on GET.

    check_agent also checks a newly accepted launch before its new audit exists;
    the previous completed audit must not hide an Agent queue/running worker.
    Killing a worker can bypass TRY/CATCH. Treat its running journal as stale
    only when Agent is inactive AND no portal writer holds the application lock.
    A second journal read avoids calling a just-completed run interrupted.
    """
    if not check_agent and (execution is None or execution.state != 'running'):
        return execution.state if execution else 'unknown'
    from .calculation_jobs import job_name
    connection=None
    try:
        connection,cursor=(connector or connect_database)('vm-dwh','DataWH')
        connection.timeout=10
        cursor.execute("""SELECT a.start_execution_date,a.stop_execution_date,a.run_requested_date,
 APPLOCK_TEST(N'public',N'PortalMotivationCalculation',N'Exclusive',N'Session')
 FROM (SELECT 1 AS probe) p LEFT JOIN msdb.dbo.sysjobs j ON j.name=?
 LEFT JOIN msdb.dbo.sysjobactivity a ON a.job_id=j.job_id
 AND a.session_id=(SELECT MAX(session_id) FROM msdb.dbo.syssessions)""",job_name(job))
        row=cursor.fetchone()
        if not row or len(row)!=4:return 'unknown'
        started,stopped,requested,lock_free=row
        if requested and (not started or stopped and requested>stopped):return 'queued'
        if started and not stopped:
            return 'running' if execution and execution.state=='running' else 'queued'
        if execution is None or execution.state!='running':
            return execution.state if execution else 'unknown'
        if lock_free!=1:return 'unknown'
        cursor.execute('SELECT State_ FROM motivation.PortalMotivationRun WHERE RunId=?',str(execution.run_id))
        current=cursor.fetchone()
        if not current:return 'unknown'
        if current[0] in ('completed','failed'):return current[0]
        return 'interrupted' if current[0]=='running' else 'unknown'
    except Exception:
        # No reply/permission is not evidence of a cancelled run.
        return 'unknown'
    finally:
        if connection is not None:connection.close()
