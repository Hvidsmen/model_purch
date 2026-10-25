"""File protocol shared by Django and the independent Windows update task."""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import time
from uuid import uuid4

RUN_PATTERN = re.compile(r'^[0-9a-f]{32}$')
STAGES = [('queued', 'Ожидание задания'), ('fetch', 'Проверка новой версии'),
          ('stop', 'Остановка портала'), ('checkout', 'Обновление кода'),
          ('dependencies', 'Обновление компонентов'), ('prepare', 'Резервная копия и миграции'),
          ('static', 'Подготовка файлов интерфейса'), ('starting', 'Запуск портала'),
          ('verify', 'Проверка готовности'), ('complete', 'Портал готов')]


def utcnow():
    return datetime.now(timezone.utc).isoformat()


def directory(root):
    return Path(root) / '.local' / 'portal-updates'


def run_directory(root, run_id):
    if not isinstance(run_id, str) or not RUN_PATTERN.fullmatch(run_id):
        raise ValueError('Invalid update identifier')
    return directory(root) / run_id


def read(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8-sig'))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid4().hex + '.tmp')
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
        for attempt in range(10):
            try:
                temporary.replace(path)
                break
            except PermissionError:
                if attempt == 9:
                    raise
                time.sleep(.03)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def lock(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as handle:
        handle.seek(0)
        if not handle.read(1):
            handle.write(b'0')
            handle.flush()
        handle.seek(0)
        acquired = False
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
            yield
        finally:
            if acquired:
                handle.seek(0)
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def configuration(root):
    config = read(Path(root) / '.local' / 'browser-updates.json')
    if not config or Path(config.get('root', '')).resolve() != Path(root).resolve():
        raise ValueError('Обновление из браузера ещё не настроено на сервере.')
    if not all(isinstance(config.get(key), str) and config[key].strip() for key in ('task_name', 'portal_task_name')):
        raise ValueError('Некорректная настройка задания обновления.')
    return config


def state(root, run_id):
    return read(run_directory(root, run_id) / 'status.json')


def active(root):
    pointer = read(directory(root) / 'active.json', {})
    run_id = pointer.get('id', '')
    return state(root, run_id) if RUN_PATTERN.fullmatch(run_id) else None


def update(root, run_id, **changes):
    data = state(root, run_id)
    if data is None:
        raise ValueError('Update not found')
    data.update(changes, updated_at=utcnow())
    write(run_directory(root, run_id) / 'status.json', data)
    return data


def progress(root, run_id, stage, detail=''):
    index = next(i for i, pair in enumerate(STAGES) if pair[0] == stage)
    data = state(root, run_id)
    events = data.get('events', [])
    events.append({'at': utcnow(), 'stage': stage, 'detail': detail or STAGES[index][1]})
    return update(root, run_id, stage=stage, detail=detail or STAGES[index][1],
                  percent=round(100 * index / (len(STAGES) - 1)), events=events[-100:])


def digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


def authorized(root, run_id, token, control=False):
    if not isinstance(token, str) or not 30 <= len(token) <= 128:
        return False
    request = read(run_directory(root, run_id) / 'request.json', {})
    expected = request.get('control_hash' if control else 'status_hash', '')
    return bool(expected) and hmac.compare_digest(expected, digest(token))



def stale(data):
    if not data or not (data['status'] == 'running' or data.get('maintenance')):
        return False
    try:
        updated = datetime.fromisoformat(data['updated_at'])
        return (datetime.now(timezone.utc) - updated).total_seconds() > 45
    except (ValueError, KeyError):
        return False


def observe(root, data):
    """Read-only detection of a killed worker after the main portal comes back."""
    if not stale(data):
        return data
    try:
        with lock(directory(root) / 'worker.lock'):
            return {**data, 'status': 'failed', 'maintenance': False, 'interrupted': True,
                'error': 'Задание обновления было прервано. Портал доступен; администратор может запустить обновление заново.'}
    except OSError:
        return data


def create(root, actor_id, actor_label):
    configuration(root)
    try:
        with lock(directory(root) / 'dispatch.lock'), lock(directory(root) / 'worker.lock'):
            current = active(root)
            if stale(current):
                # We own worker.lock here, so no live updater can be replaced.
                update(root, current['id'], status='failed', maintenance=False, finished_at=utcnow(),
                    error='Задание было прервано. Администратор запустил новое обновление.')
                current = active(root)
            if current and (current['status'] in ('queued', 'running') or current.get('maintenance')):
                raise ValueError('Обновление уже запущено. Откройте текущий запуск.')
            run_id, token, control = uuid4().hex, secrets.token_urlsafe(32), secrets.token_urlsafe(32)
            now = utcnow()
            data = {'id': run_id, 'status': 'queued', 'stage': 'queued', 'percent': 0,
                    'detail': 'Запуск отдельного задания Windows', 'actor_id': actor_id,
                    'actor': actor_label[:255], 'started_at': now, 'updated_at': now,
                    'finished_at': None, 'maintenance': False, 'attempt': 0, 'events': []}
            write(run_directory(root, run_id) / 'request.json', {'id': run_id, 'armed': False,
                  'status_hash': digest(token), 'control_hash': digest(control)})
            write(run_directory(root, run_id) / 'status.json', data)
            write(directory(root) / 'active.json', {'id': run_id})
            return data, token, control
    except OSError as error:
        raise ValueError('Задание обновления уже работает или недоступна папка состояния. Повторите просмотр статуса.') from error


def history(root, limit=20):
    folder = directory(root)
    if not folder.exists():
        return []
    records = [read(path / 'status.json') for path in folder.iterdir() if RUN_PATTERN.fullmatch(path.name)]
    return sorted([row for row in records if row], key=lambda row: row['started_at'], reverse=True)[:limit]


def revision(root):
    result = subprocess.run(['git', '-C', str(root), 'rev-parse', 'HEAD'], capture_output=True, text=True, timeout=10, check=True)
    return result.stdout.strip()


def prepared(root, run_id, database):
    """Consume a one-use preparation marker only for this checkout and database."""
    with lock(directory(root) / 'prepared.lock'):
        path = run_directory(root, run_id) / 'prepared.json'
        marker = read(path, {})
        data = state(root, run_id) or {}
        current = active(root) or {}
        if data.get('status') != 'running' or marker.get('consumed') or data.get('stage') not in ('starting', 'verify') or current.get('id') != run_id:
            return False
        selected = read(Path(root) / '.local' / 'config.json', {})
        if Path(selected.get('database_path', '')).resolve() != Path(database).resolve():
            return False
        if Path(marker.get('database', '')).resolve() != Path(database).resolve() or marker.get('revision') != revision(root):
            return False
        write(path, {**marker, 'consumed': True})
        return True


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--consume-prepared', required=True)
    parser.add_argument('--database', required=True)
    args = parser.parse_args()
    raise SystemExit(0 if prepared(Path(__file__).resolve().parent.parent, args.consume_prepared, args.database) else 1)
