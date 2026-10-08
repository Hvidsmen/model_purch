"""Stream progress for the local Django server without extra worker services."""
from contextlib import contextmanager
import json
import logging
from queue import Queue, Empty
from threading import Lock, Thread
from django.core.exceptions import ValidationError
from django.db import close_old_connections, connections
from django.http import StreamingHttpResponse

logger = logging.getLogger(__name__)
_active = set()
_guard = Lock()


@contextmanager
def scenario_operation(scenario_id):
    with _guard:
        if scenario_id in _active:
            raise ValidationError('Для этого сценария уже выполняется загрузка или расчёт. Дождитесь завершения.')
        _active.add(scenario_id)
    try:
        yield
    finally:
        with _guard:
            _active.discard(scenario_id)


def operation_response(scenario_id, action, operation):
    queue = Queue()

    def worker():
        close_old_connections()
        try:
            with scenario_operation(scenario_id):
                count = operation(scenario_id, progress=lambda data: queue.put({'type': 'progress', **data}))
                queue.put({'type': 'complete', 'percent': 100, 'count': count,
                           'stage': 'План загружен' if action == 'load' else 'Мотивация рассчитана'})
        except ValidationError as error:
            queue.put({'type': 'error', 'message': ' '.join(error.messages)})
        except Exception:
            logger.exception('Sales plan operation failed')
            queue.put({'type': 'error', 'message': 'Операция не выполнена. Проверьте журнал сервера. Сохранённые данные не изменены.'})
        finally:
            connections.close_all()

    def events():
        thread = Thread(target=worker, daemon=True, name=f'motivation-{action}-{scenario_id}')
        thread.start()
        while True:
            try:
                event = queue.get(timeout=1)
            except Empty:
                event = {'type': 'heartbeat'}
            yield json.dumps(event, ensure_ascii=False) + '\n'
            if event['type'] in ['complete', 'error']:
                break

    response = StreamingHttpResponse(events(), content_type='application/x-ndjson; charset=utf-8')
    response['Cache-Control'] = 'no-cache, no-store'
    response['X-Accel-Buffering'] = 'no'
    return response
