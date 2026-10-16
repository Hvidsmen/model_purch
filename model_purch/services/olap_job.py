"""Start SQL Agent job and wait for a new job-level history result."""
import time
import math
from django.conf import settings

JOB_STATE = """
SELECT j.job_id, s.session_id,
       COALESCE((SELECT MAX(h.instance_id) FROM msdb.dbo.sysjobhistory h
                 WHERE h.job_id = j.job_id AND h.step_id = 0), 0),
       CASE WHEN a.run_requested_date IS NOT NULL AND a.stop_execution_date IS NULL
            THEN 1 ELSE 0 END
FROM msdb.dbo.sysjobs j
CROSS JOIN (SELECT MAX(session_id) AS session_id FROM msdb.dbo.syssessions) s
LEFT JOIN msdb.dbo.sysjobactivity a ON a.job_id = j.job_id AND a.session_id = s.session_id
WHERE j.name = ?
"""
JOB_RESULT = """
SELECT TOP (1) run_status, message
FROM msdb.dbo.sysjobhistory
WHERE job_id = ? AND step_id = 0 AND instance_id > ? AND run_status IN (0, 1, 3)
ORDER BY instance_id
"""


def process_cube(connector):
    timeout = float(getattr(settings, 'OLAP_JOB_TIMEOUT_SECONDS', 3600))
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError('OLAP_JOB_TIMEOUT_SECONDS должен быть больше нуля.')
    connection, cursor = connector('vm-dwh', 'ModelPurch')
    try:
        connection.autocommit = True
        connection.timeout = 30
        cursor.execute(JOB_STATE, 'ModelPurch')
        state = cursor.fetchone()
        if state is None:
            raise RuntimeError('Задание SQL Server Agent ModelPurch не найдено или недоступно текущему пользователю.')
        job_id, session_id, previous_history, busy = state
        if session_id is None:
            raise RuntimeError('Нет активной сессии SQL Server Agent.')
        if busy:
            raise RuntimeError('Задание ModelPurch уже выполняется. Дождитесь его завершения; новый запуск не отправлен.')
        cursor.execute('EXEC msdb.dbo.sp_start_job @job_id = ?;', job_id)
        while cursor.nextset():
            pass
        deadline = time.monotonic() + timeout
        while True:
            cursor.execute('SELECT MAX(session_id) FROM msdb.dbo.syssessions')
            if cursor.fetchone()[0] != session_id:
                raise RuntimeError('SQL Server Agent перезапущен во время расчёта. Проверьте состояние задания ModelPurch.')
            cursor.execute(JOB_RESULT, job_id, previous_history)
            result = cursor.fetchone()
            if result is not None:
                status, message = result
                if status == 1:
                    return {'message': 'Задание ModelPurch успешно завершено на SQL Server.'}
                if status in (0, 3):
                    reason = 'завершилось с ошибкой' if status == 0 else 'отменено'
                    raise RuntimeError(f'Задание ModelPurch {reason}: {message or "без сообщения SQL Server Agent"}')
                # Retry/in-progress entries are not final completion.
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeError('Истекло время ожидания задания ModelPurch. Задание на SQL Server не остановлено; проверьте его состояние перед повторным запуском.')
            time.sleep(min(5, remaining))
    finally:
        connection.close()
