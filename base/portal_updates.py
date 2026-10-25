"""Narrow bridge to a fixed, independently registered Windows update task."""
from functools import lru_cache
import os
import subprocess
from django.conf import settings
from scripts import portal_update_state as protocol


def windows_supported():
    return os.name == 'nt'


def root():
    return settings.BASE_DIR


def availability():
    if not windows_supported():
        return False, 'Обновление из браузера рассчитано на Windows Task Scheduler.'
    try:
        protocol.configuration(root())
    except ValueError as error:
        return False, str(error)
    return True, ''


def create(actor):
    available, reason = availability()
    if not available:
        raise ValueError(reason)
    return protocol.create(root(), actor.pk, actor.get_full_name().strip() or actor.get_username())


def launch(run_id):
    config = protocol.configuration(root())
    with protocol.lock(protocol.directory(root()) / 'dispatch.lock'):
        current = protocol.active(root())
        if not current or current['id'] != run_id:
            raise ValueError('Этот запуск уже заменён другим обновлением.')
        if current['status'] != 'queued':
            return current  # Idempotent retries never restart a running update.
        request_path = protocol.run_directory(root(), run_id) / 'request.json'
        protocol.write(request_path, {**protocol.read(request_path, {}), 'armed': True})
        try:
            subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
                '-File', str(root() / 'scripts' / 'portal_update_task.ps1'), '-Action', 'start-updater',
                '-TaskName', config['task_name'], '-PortalTaskName', config['portal_task_name']],
                cwd=root(), stdin=subprocess.DEVNULL, capture_output=True, timeout=30, check=True)
        except (OSError, subprocess.SubprocessError):
            observed = protocol.state(root(), run_id)
            if observed and observed['status'] != 'queued':
                return observed
            protocol.update(root(), run_id, status='failed', detail='Задание обновления не запущено',
                error='Не удалось запустить отдельное задание Windows. Проверьте его регистрацию и права учётной записи.',
                finished_at=protocol.utcnow())
            raise ValueError('Не удалось запустить отдельное задание Windows. Портал продолжает работать.')
    return protocol.state(root(), run_id)


@lru_cache(maxsize=1)
def revision():
    try:
        return protocol.revision(root())
    except (OSError, subprocess.SubprocessError):
        return ''
