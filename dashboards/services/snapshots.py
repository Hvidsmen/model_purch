"""Atomic, persistent dealer snapshots and a single daily refresh job."""
from datetime import date, datetime, time, timedelta
from decimal import Decimal
import gzip
import hashlib
import json
import logging
import os
from pathlib import Path
import threading
import time as clock_time
from uuid import uuid4
from zoneinfo import ZoneInfo
from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.utils import timezone
from .source import fetch_sales, SQL_PATH

logger = logging.getLogger(__name__)
MOSCOW = ZoneInfo('Europe/Moscow')


def directory():
    return Path(getattr(settings, 'DEALER_SNAPSHOT_DIR', Path(settings.BASE_DIR) / '.local' / 'dashboards'))


def read_json(name, default=None):
    try:
        return json.loads((directory() / name).read_text(encoding='utf-8'))
    except FileNotFoundError:
        return default
    except (ValueError, UnicodeDecodeError):
        logger.warning('Invalid dashboard metadata: %s', name)
        return default


def atomic_replace(source, target):
    # Windows readers/antivirus may briefly retain a file handle.
    for attempt in range(10):
        try:
            os.replace(source, target)
            return
        except PermissionError:
            if attempt == 9:
                raise
            clock_time.sleep(0.05)


def write_json(name, value):
    folder = directory(); folder.mkdir(parents=True, exist_ok=True)
    temp = folder / (name + '.' + uuid4().hex + '.tmp')
    try:
        temp.write_text(json.dumps(value, ensure_ascii=False, default=str), encoding='utf-8')
        atomic_replace(temp, folder / name)
    finally:
        temp.unlink(missing_ok=True)


def fingerprint():
    return hashlib.sha256(SQL_PATH.read_bytes()).hexdigest()


def schedule():
    return read_json('schedule.json', {'enabled': False, 'time': '', 'timezone': 'Europe/Moscow'})


def save_schedule(enabled, value):
    try:
        clock = time.fromisoformat(value) if value else None
    except ValueError:
        raise ValidationError('Укажите время обновления в формате ЧЧ:ММ.')
    if enabled and clock is None:
        raise ValidationError('Для ежедневного запуска укажите время по Москве.')
    if clock and (clock.second or clock.microsecond):
        raise ValidationError('Укажите время с точностью до минуты.')
    result = {'enabled': bool(enabled), 'time': clock.strftime('%H:%M') if clock else '', 'timezone': 'Europe/Moscow'}
    write_json('schedule.json', result)
    return result


def status():
    manifest = read_json('current.json')
    run = read_json('run.json', {'state': 'never', 'rows': 0})
    if run.get('state') == 'running':
        probe = acquire_lock()
        if probe is not None:
            release_lock(probe)
            run = {**run, 'state': 'interrupted', 'stage': 'Обновление прервано', 'error': 'Процесс обновления завершился или портал был перезапущен. Можно запустить обновление повторно.'}
    return {'schedule': schedule(), 'run': run, 'snapshot': manifest,
            'source_changed': bool(manifest and manifest.get('query_hash') != fingerprint())}


def acquire_lock():
    folder = directory(); folder.mkdir(parents=True, exist_ok=True)
    handle = (folder / 'refresh.lock').open('a+b')
    if handle.tell() == 0:
        handle.write(b'0'); handle.flush()
    handle.seek(0)
    try:
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    return handle


def release_lock(handle):
    try:
        handle.seek(0)
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()


def run_refresh(handle=None):
    handle = handle or acquire_lock()
    if handle is None:
        return False
    try:
        return _refresh_locked()
    finally:
        release_lock(handle)


def _refresh_locked():
    started = timezone.now().isoformat()
    run = {'state': 'running', 'stage': 'Чтение DataWH', 'started_at': started, 'rows': 0}
    write_json('run.json', run)
    history = read_json('history.json', [])
    snapshot_path = None
    try:
        as_of = timezone.now().astimezone(MOSCOW).date()
        def progress(count):
            run.update(rows=count, stage='Чтение месячных продаж')
            write_json('run.json', run)
        source = fetch_sales(as_of, force=True, progress=progress)
        run.update(stage='Сохранение снимка', rows=len(source['rows']))
        write_json('run.json', run)
        token = uuid4().hex
        snapshot_path = directory() / ('data-' + token + '.json.gz')
        temporary = snapshot_path.with_suffix('.tmp')
        try:
            with gzip.open(temporary, 'wt', encoding='utf-8', compresslevel=3) as output:
                json.dump(source['rows'], output, ensure_ascii=False, default=str)
            atomic_replace(temporary, snapshot_path)
        finally:
            temporary.unlink(missing_ok=True)
        manifest = {'file': snapshot_path.name, 'loaded_at': source['loaded_at'], 'end_exclusive': source['end_exclusive'],
                    'as_of': as_of.isoformat(), 'rows': len(source['rows']), 'query_hash': fingerprint()}
        # The old manifest remains valid until the complete gzip has been published.
        write_json('current.json', manifest)
        run.update(state='completed', stage='Готово', completed_at=timezone.now().isoformat())
        write_json('run.json', run)
        write_json('history.json', [run, *history][:30])
        snapshots = sorted(directory().glob('data-*.json.gz'), key=lambda path: path.stat().st_mtime_ns, reverse=True)
        for old in snapshots[2:]:
            if old.name != manifest['file']:
                try: old.unlink()
                except OSError: pass  # A reader may still have the previous file open on Windows.
        return True
    except Exception:
        logger.exception('Dealer daily refresh failed')
        run.update(state='error', stage='Ошибка обновления', completed_at=timezone.now().isoformat(),
                   error='Не удалось обновить данные из DataWH. Последний успешный снимок сохранён. Подробности — в журнале портала.')
        write_json('run.json', run)
        write_json('history.json', [run, *history][:30])
        return False


def start_refresh():
    handle = acquire_lock()
    if handle is None:
        return False
    try:
        write_json('run.json', {'state': 'running', 'stage': 'Запуск обновления', 'started_at': timezone.now().isoformat(), 'rows': 0})
        worker = threading.Thread(target=run_refresh, args=(handle,), daemon=True, name='dealer-dashboard-refresh')
        worker.start()
    except Exception:
        release_lock(handle)
        raise
    return True


def read_snapshot():
    manifest = read_json('current.json')
    if not manifest:
        raise ValidationError('Данные ещё не подготовлены. Откройте «Обновление данных» и запустите первое обновление.')
    filename = manifest['file']
    if Path(filename).name != filename or not filename.startswith('data-') or not filename.endswith('.json.gz'):
        raise ValidationError('Некорректный файл снимка. Выполните обновление данных.')
    key = 'dealer-snapshot:' + str(directory()) + ':' + filename
    source = cache.get(key)
    if source is None:
        with gzip.open(directory() / filename, 'rt', encoding='utf-8') as input_file:
            rows = json.load(input_file)
        for row in rows:
            row['month'] = date.fromisoformat(row['month'])
            row['quantity'] = Decimal(row['quantity']); row['amount'] = Decimal(row['amount'])
        source = {'rows': rows, 'loaded_at': manifest['loaded_at'], 'end_exclusive': manifest['end_exclusive'], 'as_of': manifest['as_of']}
        cache.set(key, source, 24 * 60 * 60)
    source['refresh_status'] = status()['run']
    source['source_changed'] = manifest.get('query_hash') != fingerprint()
    return source


def due(now, config, last_run):
    if not config.get('enabled') or not config.get('time'):
        return False
    now = now.astimezone(MOSCOW)
    target = datetime.combine(now.date(), time.fromisoformat(config['time']), MOSCOW)
    if now < target:
        target -= timedelta(days=1)
    started = last_run.get('started_at')
    return not started or datetime.fromisoformat(started).astimezone(MOSCOW) < target


def scheduler_loop(stop):
    # Schedule stays disabled until the operator sets the source completion time.
    while not stop.wait(30):
        try:
            if due(timezone.now(), schedule(), read_json('run.json', {})):
                run_refresh()
        except Exception:
            logger.exception('Dealer refresh scheduler failed')
