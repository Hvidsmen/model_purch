"""Durable SQL Server Agent jobs for the portal's motivation calculation."""
import re
from decimal import Decimal, InvalidOperation
from datetime import timedelta
from zoneinfo import ZoneInfo
from django.core.exceptions import ValidationError
from ..conns import connect_database
from .subdivision_aliases import rules

PROC = 'motivation.PortalCalcBySub'
LOCKED_COMMAND = """DECLARE @result int;
EXEC @result = sys.sp_getapplock @Resource=N'PortalMotivationCalculation',
 @LockMode=N'Exclusive', @LockOwner=N'Session', @LockTimeout=0;
IF @result < 0 THROW 50001, N'Расчёт мотивации уже выполняется', 1;
EXEC motivation.PortalCalcBySub;
EXEC sys.sp_releaseapplock @Resource=N'PortalMotivationCalculation', @LockOwner=N'Session';"""


def calculation_command(job):
    if type(job.fact_year) is not int or not 1 <= job.fact_year <= 9999:
        raise ValidationError('Укажите год факта в параметрах задания.')
    plan = (job.plan_version or '').strip()
    if not plan or len(plan) > 500:
        raise ValidationError('Укажите версию плана продаж в параметрах задания.')
    try:
        quarter, year = Decimal(str(job.quarter_share)), Decimal(str(job.year_share))
        if not all(value.is_finite() and 0 <= value <= 100 for value in (quarter, year)) or quarter + year != 100:
            raise ValueError()
        fractions = [value / 100 for value in (quarter, year)]
        if any(value != value.quantize(Decimal('0.00000001')) for value in fractions):
            raise ValueError()
    except (InvalidOperation, ValueError, TypeError):
        raise ValidationError('Доли должны быть от 0 до 100%, в сумме 100%, не более 6 знаков после запятой.')
    # SQL Agent stores a command string, so quote the only text literal explicitly.
    escaped_plan = plan.replace("'", "''")
    statement = (f"EXEC motivation.PortalCalcBySub @FactYear={job.fact_year}, "
                 f"@PlanVersion=N'{escaped_plan}', "
                 f"@QuarterShare={fractions[0]:.8f}, @YearShare={fractions[1]:.8f};")
    return LOCKED_COMMAND.replace('EXEC motivation.PortalCalcBySub;', statement)


def validate_sql_parameters(cursor, job):
    cursor.execute("SELECT name FROM sys.parameters WHERE object_id=OBJECT_ID(N'motivation.PortalCalcBySub')")
    names = {row[0].casefold() for row in cursor.fetchall()}
    if not {'@factyear', '@planversion', '@quartershare', '@yearshare'} <= names:
        raise ValidationError('Процедура MS SQL не поддерживает параметры года, версии плана и долей. Установите параметризованную версию sp_CalcBySub. Задание не изменено.')
    cursor.execute('SELECT TOP (1) 1 FROM planning.PlanSales WHERE Version_=?', job.plan_version.strip())
    if not cursor.fetchone():
        raise ValidationError('Указанная версия плана не найдена в MS SQL planning.PlanSales. Проверьте точное название.')


def job_name(job):
    return 'PortalMotivation_' + job.key.hex


def quote(name):
    return '[' + name.replace(']', ']]') + ']'


def prepare(cursor):
    cursor.execute("SELECT OBJECT_DEFINITION(OBJECT_ID(N'motivation.sp_CalcBySub')), OBJECT_ID(N'motivation.PortalCalcBySub')")
    definition, existing = cursor.fetchone()
    if not definition:
        raise ValidationError('Не найдена процедура motivation.sp_CalcBySub или нет права VIEW DEFINITION.')
    cursor.execute("SELECT name FROM sys.columns WHERE object_id=OBJECT_ID(N'dbo.Subdivisions') ORDER BY column_id")
    columns = [row[0] for row in cursor.fetchall()]
    if 'SubdivisionName' not in columns:
        raise ValidationError('Не найден справочник dbo.Subdivisions.SubdivisionName.')
    cursor.execute("""IF OBJECT_ID(N'motivation.PortalSubdivisionMerge', N'U') IS NULL
    CREATE TABLE motivation.PortalSubdivisionMerge (SourceName nvarchar(255) NOT NULL PRIMARY KEY, TargetName nvarchar(255) NOT NULL);
    DELETE FROM motivation.PortalSubdivisionMerge;""")
    config = rules()
    if config:
        cursor.executemany('INSERT INTO motivation.PortalSubdivisionMerge (SourceName,TargetName) VALUES (?,?)', [(source, target) for source, target, policy in config])
    cursor.execute("IF OBJECT_ID(N'motivation.PortalCalculationSubdivisions', N'V') IS NULL EXEC(N'CREATE VIEW motivation.PortalCalculationSubdivisions AS SELECT 1 AS placeholder');")
    projections = [f'COALESCE(m.TargetName,s.{quote(column)}) AS {quote(column)}' if column == 'SubdivisionName' else 's.' + quote(column) for column in columns]
    cursor.execute('ALTER VIEW motivation.PortalCalculationSubdivisions AS SELECT ' + ','.join(projections) +
                   ' FROM dbo.Subdivisions s LEFT JOIN motivation.PortalSubdivisionMerge m ON m.SourceName=s.SubdivisionName;')
    header = re.compile(r'\b(?:CREATE\s+(?:OR\s+ALTER\s+)?|ALTER\s+)PROC(?:EDURE)?\s+(?:\[?motivation\]?\s*\.\s*)?(?:\[sp_CalcBySub\]|sp_CalcBySub\b)', re.I)
    changed, count = header.subn(('ALTER' if existing else 'CREATE') + ' PROCEDURE motivation.PortalCalcBySub', definition, count=1)
    if count != 1:
        raise ValidationError('Не удалось распознать определение sp_CalcBySub. Исходная процедура не изменена.')
    changed, references = re.subn(r'(?:(?:\[?DataWH\]?)\s*\.\s*)?\[?dbo\]?\s*\.\s*\[?Subdivisions\]?(?!\w)',
                                  'DataWH.motivation.PortalCalculationSubdivisions', changed, flags=re.I)
    if config and not references:
        raise ValidationError('В процедуре не найдена точка объединения подразделений. Расчёт не запущен.')
    from ..models import SubdivisionCoeff
    from .pilot_formula import GLOBAL_SUBDIVISION, patch_calculation
    if SubdivisionCoeff.objects.filter(version__status='approved', variation_calculate__variation_name=GLOBAL_SUBDIVISION).exists():
        changed = patch_calculation(changed)
    cursor.execute(changed)


def sync_job(job, connector=None):
    command = calculation_command(job)
    connection = None
    try:
        connection, cursor = (connector or connect_database)('vm-dwh', 'DataWH')
        connection.autocommit = False
        connection.timeout = 120
        from ..models import SubdivisionMergeLog
        if any(not row.details.get('sql_synced') for row in SubdivisionMergeLog.objects.all()):
            raise ValidationError('Сначала синхронизируйте объединение подразделений с SQL Server на странице объединения.')
        cursor.execute("""DECLARE @r int; EXEC @r=sys.sp_getapplock
          @Resource=N'PortalMotivationCalculation',@LockMode=N'Exclusive',@LockOwner=N'Session',@LockTimeout=0;
          IF @r < 0 THROW 50001,N'Дождитесь завершения расчёта мотивации',1;""")
        prepare(cursor)
        validate_sql_parameters(cursor, job)
        name = job_name(job)
        cursor.execute('SELECT job_id FROM msdb.dbo.sysjobs WHERE name=?', name)
        exists = cursor.fetchone()
        if not exists:
            cursor.execute('EXEC msdb.dbo.sp_add_job @job_name=?, @enabled=1, @description=?', name, 'Расчёт мотивации из портала')
            cursor.execute('EXEC msdb.dbo.sp_add_jobstep @job_name=?, @step_name=?, @subsystem=N\'TSQL\', @database_name=N\'DataWH\', @command=?', name, 'Расчёт мотивации', command)
            cursor.execute('EXEC msdb.dbo.sp_add_jobserver @job_name=?', name)
        else:
            cursor.execute('EXEC msdb.dbo.sp_update_jobstep @job_name=?, @step_id=1, @command=?', name, command)
        cursor.execute('SELECT schedule_id FROM msdb.dbo.sysschedules WHERE name=?', name)
        schedule = cursor.fetchone()
        if schedule:
            cursor.execute('EXEC msdb.dbo.sp_detach_schedule @job_name=?, @schedule_id=?, @delete_unused_schedule=1', name, schedule[0])
        if job.enabled and job.mode != 'manual':
            if job.run_at is None:
                raise ValidationError('Укажите дату и время расписания.')
            # Agent uses server-local time. SQL converts an aware Moscow instant
            # using the server's current UTC offset (DST-free server schedules).
            cursor.execute('SELECT DATEPART(TZOFFSET,SYSDATETIMEOFFSET())')
            offset = int(cursor.fetchone()[0])
            instant = job.run_at.astimezone(ZoneInfo('UTC')).replace(tzinfo=None) + timedelta(minutes=offset)
            cursor.execute('EXEC msdb.dbo.sp_add_jobschedule @job_name=?, @name=?, @freq_type=?, @freq_interval=1, @active_start_date=?, @active_start_time=?',
                           name, name, 4 if job.mode == 'daily' else 1, int(instant.strftime('%Y%m%d')), int(instant.strftime('%H%M%S')))
        connection.commit()
    except Exception:
        if connection is not None:
            connection.rollback()
        raise
    finally:
        if connection is not None:
            connection.close()


def start_job(job, connector=None):
    sync_job(job, connector)
    connection = None
    try:
        connection, cursor = (connector or connect_database)('vm-dwh', 'DataWH')
        cursor.execute('EXEC msdb.dbo.sp_start_job @job_name=?', job_name(job))
        connection.commit()
    finally:
        if connection is not None:
            connection.close()


def status(job, connector=None):
    connection = None
    try:
        connection, cursor = (connector or connect_database)('vm-dwh', 'DataWH')
        cursor.execute("""SELECT a.start_execution_date,a.stop_execution_date,h.run_status,h.message
        FROM msdb.dbo.sysjobs j
        LEFT JOIN msdb.dbo.sysjobactivity a ON a.job_id=j.job_id AND a.session_id=(SELECT MAX(session_id) FROM msdb.dbo.syssessions)
        OUTER APPLY (SELECT TOP (1) run_status,message FROM msdb.dbo.sysjobhistory
          WHERE job_id=j.job_id AND step_id=0 ORDER BY instance_id DESC) h
        WHERE j.name=?""", job_name(job))
        row = cursor.fetchone()
        if not row:
            return 'Не настроено в SQL Agent'
        started, stopped, result, message = row
        if started and not stopped:
            return 'Выполняется'
        return {0: 'Ошибка', 1: 'Завершено', 3: 'Отменено'}.get(result, 'Ещё не запускалось')
    finally:
        if connection is not None:
            connection.close()


def sync_merge_configuration(connector=None):
    from .sql_export import export_motivation
    from ..models import SubdivisionMergeLog
    connection = None
    try:
        connection, cursor = (connector or connect_database)('vm-dwh', 'DataWH')
        connection.autocommit = False
        connection.timeout = 120
        cursor.execute("""DECLARE @r int; EXEC @r=sys.sp_getapplock
          @Resource=N'PortalMotivationCalculation',@LockMode=N'Exclusive',@LockOwner=N'Session',@LockTimeout=0;
          IF @r < 0 THROW 50001,N'Дождитесь завершения расчёта мотивации',1;""")
        prepare(cursor)
        export_motivation(lambda server, database: (connection, cursor), manage_connection=False)
        connection.commit()
    except Exception:
        if connection is not None:
            connection.rollback()
        raise
    finally:
        if connection is not None:
            connection.close()
    for log in SubdivisionMergeLog.objects.all():
        log.details = {**log.details, 'sql_synced': True}
        log.save(update_fields=['details'])
