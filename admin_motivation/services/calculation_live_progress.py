"""Read the current Agent request, without changing a running calculation."""
import re

SQL="""SELECT TOP(1) r.session_id,r.status,r.command,r.wait_type,r.wait_time,
 r.blocking_session_id,r.cpu_time,r.logical_reads,
 OBJECT_NAME(t.objectid,t.dbid),
 SUBSTRING(t.text,r.statement_start_offset/2+1,
  (CASE WHEN r.statement_end_offset=-1 THEN DATALENGTH(t.text)
        ELSE r.statement_end_offset END-r.statement_start_offset)/2+1)
FROM msdb.dbo.sysjobs j
JOIN sys.dm_exec_sessions s ON s.program_name LIKE
 N'SQLAgent - TSQL JobStep (Job 0x'+CONVERT(nvarchar(32),CONVERT(binary(16),j.job_id),2)+N' : Step %'
JOIN sys.dm_exec_requests r ON r.session_id=s.session_id
OUTER APPLY sys.dm_exec_sql_text(r.sql_handle) t
WHERE j.name=? ORDER BY r.start_time DESC;"""


def phase(statement):
    code=str(statement or '').casefold()
    if 'portalpreparedsales' in code and '#portal_motivation_sales' in code:return 'Копирование подготовленного факта'
    if '#percent_plan' in code:return 'Распределение плана по авторам'
    if 'annualaccrual' in code:return 'Расчёт годовой части'
    if 'reportmotivationbydealauthor' in code:return 'Расчёт квартальных и годовой премий'
    if 'planfact' in code or '#pf_base' in code:return 'Объединение плана и факта, базы выполнения'
    if re.search(r'(?:#fact\b|motivation\]?\.\[?fact\b)',code):return 'Расчёт факта и сегментов скидок'
    if 'portal_motivation_sales' in code:return 'Обработка факта продаж'
    if 'coeff' in code:return 'Подбор коэффициентов'
    if 'portalmotivationrun' in code:return 'Регистрация результата'
    return 'Выполняется SQL-операция; этап не распознан'


def read(cursor,name):
    try:
        cursor.execute(SQL,name)
        row=cursor.fetchone()
        if not row or len(row)!=10:return {'stage':'SQL Agent выполняет задание; текущая операция пока недоступна'}
        session,status,command,wait,wait_ms,blocker,cpu,reads,procedure,statement=row
        proc=str(procedure or '').casefold();scope=''
        if 'separate2026' in proc:scope='Раздельный сценарий · '
        elif 'all2026' in proc:scope='Объединённый сценарий · '
        elif 'portalcalcbySub'.casefold() in proc:scope='Основной вариант · '
        waiting=''
        if blocker:waiting=f'Ожидает снятия блокировки: сеанс {int(blocker)}'
        elif wait:
            wait=str(wait)
            if wait.startswith(('PAGEIOLATCH','IO_COMPLETION','ASYNC_IO_COMPLETION')):waiting='Ожидание диска / ввода-вывода'
            elif wait in ('CXPACKET','CXCONSUMER'):waiting='Параллельная обработка'
            else:waiting='Ожидание SQL Server: '+wait
        return {'stage':scope+phase(statement),'waiting':waiting,'session_id':int(session),
            'wait_ms':int(wait_ms or 0),'cpu_ms':int(cpu or 0),'logical_reads':int(reads or 0)}
    except Exception:
        # Missing VIEW SERVER STATE must not hide Agent status or expose SQL.
        return {'stage':'SQL Agent выполняет задание; нет доступа к текущей SQL-операции'}
