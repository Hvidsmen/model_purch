"""Diagnostic progress only: never used to decide financial readiness."""
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from django.core.exceptions import ValidationError
from django.utils import timezone as clock
from ..conns import connect_database

from . import calculation_stage_profile as profile

TABLE='motivation.PortalRecalculation2025Progress'
DDL=f"""IF OBJECT_ID(N'{TABLE}',N'U') IS NULL
 CREATE TABLE {TABLE}(RunId uniqueidentifier NOT NULL PRIMARY KEY,
 Stage_ nvarchar(150) NOT NULL,StageStartedAt datetime2 NOT NULL);"""
DDL += profile.DDL


def begin_sql():
    return f"""EXEC sys.sp_set_session_context @key=N'PortalRecalculation2025Run',@value=@portalRun;
 EXEC sys.sp_set_session_context @key=N'PortalRecalculation2025Stage',@value=N'Начало расчёта';
 EXEC sys.sp_set_session_context @key=N'PortalRecalculation2025StageAt',@value=@portalStarted;
 INSERT INTO {TABLE}(RunId,Stage_,StageStartedAt) VALUES(@portalRun,N'Начало расчёта',SYSUTCDATETIME());"""+profile.begin('@portalRun',('Начало расчёта',*STAGES))


def finish_sql(failed=False):
    label="COALESCE(CONVERT(nvarchar(150),SESSION_CONTEXT(N'PortalRecalculation2025Stage')),N'Начало расчёта')" if failed else "N'Расчёт завершён'"
    # After rollback preserve the stage from session context, whose value does
    # not roll back with the procedure's transaction. Keep its original time.
    return profile.finish(('Начало расчёта',*STAGES),failed=failed)+f"""UPDATE {TABLE} SET Stage_={label},StageStartedAt=COALESCE(
 TRY_CONVERT(datetime2,SESSION_CONTEXT(N'PortalRecalculation2025StageAt')),StageStartedAt)
 WHERE RunId=@portalRun;
 EXEC sys.sp_set_session_context @key=N'PortalRecalculation2025Run',@value=NULL;"""


def stage_sql(label):
    # Labels are fixed application constants. No user input is interpolated.
    return f"""\n-- Portal 2025 progress: {label}
IF SESSION_CONTEXT(N'PortalRecalculation2025Run') IS NOT NULL
BEGIN
 DECLARE @portalStageAt_{stage_id(label)} datetime2=SYSUTCDATETIME();
 EXEC sys.sp_set_session_context @key=N'PortalRecalculation2025Stage',@value=N'{label}';
 EXEC sys.sp_set_session_context @key=N'PortalRecalculation2025StageAt',@value=@portalStageAt_{stage_id(label)};
 UPDATE {TABLE} SET Stage_=N'{label}',StageStartedAt=@portalStageAt_{stage_id(label)}
 WHERE RunId=TRY_CONVERT(uniqueidentifier,SESSION_CONTEXT(N'PortalRecalculation2025Run'));
END;\n"""+profile.mark(stage_id(label),f'recalc_{stage_id(label)}')


STAGES=('Загрузка коэффициентов файла','Исторические формулы 2025','Чтение подготовленного факта',
        'Расчёт факта и сегментов скидок','Распределение плана по долям факта',
        'Объединение плана и факта','Премии кварталов, года и доплаты','Сохранение результатов')


def stage_id(label):return STAGES.index(label)+1


def patch(source):
    from .recalculation_2025_sql import SETUP,FORMULA_SNAPSHOT,ALLOCATION
    from .prepared_sales import MARKER
    anchors=(SETUP,FORMULA_SNAPSHOT,MARKER,';WITH fact AS (',ALLOCATION,
        "IF OBJECT_ID('DataWH.motivation.PlanFactTest2025')",
        "IF OBJECT_ID('DataWH.motivation.ReportMotivationByDealAuthorTest2025')",'COMMIT TRANSACTION;')
    if '-- Portal 2025 progress:' in source:return source
    points=[]
    for label,anchor in zip(STAGES,anchors):
        if source.count(anchor)!=1:
            raise ValidationError('Не распознана граница этапа пересчёта 2025: '+label)
        points.append((source.index(anchor),stage_sql(label)))
    if [p for p,_ in points]!=sorted(p for p,_ in points):
        raise ValidationError('Изменён порядок этапов пересчёта 2025.')
    for position,sql in reversed(points):source=source[:position]+sql+source[position:]
    return source


def aware(value):
    return value.replace(tzinfo=timezone.utc) if value and clock.is_naive(value) else value


def display(job,execution,state,connector=None,now=None):
    if execution is None or not isinstance(getattr(execution,'started_at',None),datetime):return None
    started=aware(execution.started_at);completed=aware(getattr(execution,'completed_at',None))
    now=now or clock.now()
    result={'started':clock.localtime(started,ZoneInfo('Europe/Moscow')),
        'completed':clock.localtime(completed,ZoneInfo('Europe/Moscow')) if completed else None,
        'elapsed':max(0,int(((completed or now)-started).total_seconds())),
        'ticking':state=='running','stage':'','stage_elapsed':None,'stage_ticking':False,
        'interrupted':state=='interrupted','state_unknown':state=='unknown'}
    connection=None
    try:
        connection,cursor=(connector or connect_database)('vm-dwh','DataWH');connection.timeout=3
        # The procedure updates this diagnostic row inside its transaction.
        # Dirty reads are intentional ONLY here, never for financial data or
        # completion decisions. Re-reading the audit prevents stage rows from a
        # different attempt or a completion race affecting the displayed time.
        cursor.execute(f"""IF OBJECT_ID(N'{TABLE}',N'U') IS NOT NULL
 SELECT p.Stage_,p.StageStartedAt,r.CompletedAt FROM {TABLE} p WITH (READUNCOMMITTED)
 JOIN motivation.PortalMotivationRun r ON r.RunId=p.RunId
 WHERE p.RunId=? AND r.JobKey=?""",str(execution.run_id),str(job.key))
        row=cursor.fetchone()
        if row:
            label,stage_at,finished=row;finished=aware(finished)
            if finished:
                result['completed']=clock.localtime(finished,ZoneInfo('Europe/Moscow'))
                result['elapsed']=max(0,int((finished-started).total_seconds()));result['ticking']=False
            result['stage']=label
            if isinstance(stage_at,datetime):
                result['stage_elapsed']=max(0,int(((finished or completed or now)-aware(stage_at)).total_seconds()))
                result['stage_ticking']=result['ticking']
        result['stages']=profile.read(cursor,execution.run_id)
        if result['ticking']:
            from .calculation_jobs import job_name
            from .calculation_live_progress import read as live_read
            result['live']=live_read(cursor,job_name(job))
    except Exception:
        # Older/in-flight jobs cannot gain instrumentation retroactively.
        # Their recorded start time is still useful; never invent an SQL phase.
        pass
    finally:
        if connection is not None:connection.close()
    return result
