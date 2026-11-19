"""Persist the preparation clock and serialize dependent Agent calculations."""
from datetime import time
from django.core.exceptions import ValidationError

DAILY_JOB='PortalMotivation_PrepareSales_Daily'
SCHEDULE='PortalMotivation_PrepareSales_Moscow'
DDL="""IF OBJECT_ID(N'motivation.PortalPreparationSchedule',N'U') IS NULL
 CREATE TABLE motivation.PortalPreparationSchedule(Id int PRIMARY KEY CHECK(Id=1),MoscowTime time(0) NOT NULL,Enabled bit NOT NULL);
IF NOT EXISTS(SELECT 1 FROM motivation.PortalPreparationSchedule WHERE Id=1)
 INSERT motivation.PortalPreparationSchedule VALUES(1,'09:00',1);
IF OBJECT_ID(N'motivation.PortalPreparationFollowers',N'U') IS NULL
 CREATE TABLE motivation.PortalPreparationFollowers(JobName sysname PRIMARY KEY);
"""

# Each calculation keeps its own Agent identity, monitoring, and history. Wait for
# its terminal outcome before starting the next, so the shared lock cannot race.
FOLLOWERS="""SET NOCOUNT ON;
DECLARE @Name sysname,@Job uniqueidentifier,@Before int,@Outcome int;
DECLARE followers CURSOR LOCAL FAST_FORWARD FOR
 SELECT f.JobName,j.job_id FROM motivation.PortalPreparationFollowers f
 JOIN msdb.dbo.sysjobs j ON j.name=f.JobName AND j.enabled=1
 ORDER BY f.JobName;
OPEN followers;
FETCH NEXT FROM followers INTO @Name,@Job;
WHILE @@FETCH_STATUS=0
BEGIN
 IF EXISTS(SELECT 1 FROM msdb.dbo.sysjobactivity WHERE job_id=@Job
 AND session_id=(SELECT MAX(session_id) FROM msdb.dbo.syssessions)
 AND start_execution_date IS NOT NULL AND stop_execution_date IS NULL)
 THROW 50040,N'Зависимый расчёт уже выполняется; повторный запуск не выполнен.',1;
 SELECT @Before=COALESCE(MAX(instance_id),0) FROM msdb.dbo.sysjobhistory WHERE job_id=@Job AND step_id=0;
 EXEC msdb.dbo.sp_start_job @job_id=@Job;
 SET @Outcome=NULL;
 WHILE @Outcome IS NULL
 BEGIN
  WAITFOR DELAY '00:00:02';
  SELECT TOP(1) @Outcome=run_status FROM msdb.dbo.sysjobhistory
   WHERE job_id=@Job AND step_id=0 AND instance_id>@Before AND run_status IN(0,1,3) ORDER BY instance_id;
 END;
 IF @Outcome<>1 THROW 50040,N'Ежедневный расчёт завершился ошибкой или отменён. Следующие задания не запущены.',1;
 FETCH NEXT FROM followers INTO @Name,@Job;
END;
CLOSE followers;
DEALLOCATE followers;
"""


def detach(cursor,name):
    # Remove every clock attached to this portal-owned job. Custom/shared
    # schedules survive if other jobs still reference them.
    cursor.execute("""DECLARE @Schedule int;
DECLARE clocks CURSOR LOCAL FAST_FORWARD FOR SELECT js.schedule_id FROM msdb.dbo.sysjobschedules js
 JOIN msdb.dbo.sysjobs j ON j.job_id=js.job_id WHERE j.name=?;
OPEN clocks; FETCH NEXT FROM clocks INTO @Schedule;
WHILE @@FETCH_STATUS=0
BEGIN
 EXEC msdb.dbo.sp_detach_schedule @job_name=?,@schedule_id=@Schedule,@delete_unused_schedule=1;
 FETCH NEXT FROM clocks INTO @Schedule;
END;
CLOSE clocks; DEALLOCATE clocks;""",name,name)


def register(cursor,name,enabled):
    cursor.execute(DDL)
    cursor.execute('DELETE FROM motivation.PortalPreparationFollowers WHERE JobName=?',name)
    if enabled:
        cursor.execute('INSERT motivation.PortalPreparationFollowers(JobName) VALUES(?)',name)
        detach(cursor,name)


def install(cursor):
    from .prepared_sales import JOB,PROC
    cursor.execute(DDL)
    # The existing preparation job remains a manual-only job.
    detach(cursor,JOB)
    cursor.execute("""DECLARE @Job uniqueidentifier;
SELECT @Job=job_id FROM msdb.dbo.sysjobs WHERE name=?;
IF @Job IS NULL
BEGIN
 EXEC msdb.dbo.sp_add_job @job_name=?,@enabled=1,@job_id=@Job OUTPUT;
 EXEC msdb.dbo.sp_add_jobstep @job_id=@Job,@step_name=N'Подготовка факта',@database_name=N'DataWH',@subsystem=N'TSQL',@flags=4,@command=?,@on_success_action=3,@on_fail_action=2;
 EXEC msdb.dbo.sp_add_jobstep @job_id=@Job,@step_name=N'Ежедневные расчёты после подготовки',@database_name=N'DataWH',@subsystem=N'TSQL',@flags=4,@command=?,@on_success_action=1,@on_fail_action=2;
 EXEC msdb.dbo.sp_add_jobserver @job_id=@Job;
END
ELSE
BEGIN
 EXEC msdb.dbo.sp_update_jobstep @job_id=@Job,@step_id=1,@command=?,@on_success_action=3,@on_fail_action=2;
 EXEC msdb.dbo.sp_update_jobstep @job_id=@Job,@step_id=2,@command=?,@on_success_action=1,@on_fail_action=2;
END;""",DAILY_JOB,DAILY_JOB,f'EXEC {PROC};',FOLLOWERS,f'EXEC {PROC};',FOLLOWERS)
    apply_clock(cursor)


def apply_clock(cursor):
    detach(cursor,DAILY_JOB)
    cursor.execute("""DECLARE @Clock time(0),@Enabled bit,@Time int;
SELECT @Clock=MoscowTime,@Enabled=Enabled FROM motivation.PortalPreparationSchedule WHERE Id=1;
SET @Time=CONVERT(int,REPLACE(CONVERT(varchar(8),DATEADD(minute,DATEPART(TZOFFSET,SYSDATETIMEOFFSET())-180,CONVERT(datetime,@Clock)),108),':',''));
EXEC msdb.dbo.sp_add_jobschedule @job_name=?,@name=?,@enabled=@Enabled,@freq_type=4,@freq_interval=1,@active_start_time=@Time;""",DAILY_JOB,SCHEDULE)


def save(clock,enabled,connector=None):
    from ..conns import connect_database
    from . import prepared_sales,calculation_jobs
    from ..models import MotivationCalculationSchedule
    if not isinstance(clock,time) or clock.tzinfo is not None:raise ValidationError('Укажите время по Москве.')
    connection=None
    try:
        connection,cursor=(connector or connect_database)('vm-dwh','DataWH');connection.timeout=120
        cursor.execute("DECLARE @r int; EXEC @r=sys.sp_getapplock @Resource=N'PortalMotivationCalculation',@LockMode=N'Exclusive',@LockOwner=N'Session',@LockTimeout=0; IF @r<0 THROW 50001,N'Дождитесь завершения расчёта или подготовки факта',1;")
        prepared_sales.install(cursor)
        cursor.execute('UPDATE motivation.PortalPreparationSchedule SET MoscowTime=?,Enabled=? WHERE Id=1',clock,bool(enabled))
        # Migrate already configured daily jobs away from independent clocks.
        for job in MotivationCalculationSchedule.objects.filter(synced=True):
            register(cursor,calculation_jobs.job_name(job),job.enabled and job.mode=='daily')
        apply_clock(cursor)
        connection.commit()
    except Exception:
        if connection is not None:connection.rollback()
        raise
    finally:
        if connection is not None:connection.close()


def read(cursor):
    cursor.execute("IF OBJECT_ID(N'motivation.PortalPreparationSchedule',N'U') IS NOT NULL SELECT CONVERT(varchar(5),MoscowTime,108),Enabled,CONVERT(bit,1) FROM motivation.PortalPreparationSchedule WHERE Id=1; ELSE SELECT '09:00',CONVERT(bit,1),CONVERT(bit,0);")
    row=cursor.fetchone()
    return {'time':row[0],'enabled':bool(row[1]),'configured':bool(row[2]) if len(row)>2 else True}
