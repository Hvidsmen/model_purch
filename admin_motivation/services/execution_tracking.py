"""Record the actual export and parameters used by SQL Agent, including scheduled runs."""
import json
from datetime import timezone
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
    return {'fact_year':job.fact_year,'plan_version':job.plan_version,
            'quarter_share':str(job.quarter_share),'year_share':str(job.year_share),
            'payout_threshold':str(job.payout_threshold)}


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
        execution,_=MotivationExecution.objects.update_or_create(run_id=run,defaults=values)
        return execution
    except Exception:
        # Old Agent steps have no audit yet. Never infer a completed run from a timer.
        return None
    finally:
        if connection is not None:
            connection.close()
