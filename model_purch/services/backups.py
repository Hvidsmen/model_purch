"""Consistent SQLite snapshots, including committed WAL data."""
import sqlite3
import logging
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
from time import monotonic

# Python 3.10 does not expose these stable SQLite result codes.
SQLITE_DONE = 101
SQLITE_BUSY = 5
SQLITE_LOCKED = 6


def backup_sqlite(database, directory, notify=None):
    database = Path(database).resolve()
    if not database.is_file():
        raise FileNotFoundError(f'База не найдена: {database}')
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    destination = directory / f'{database.stem}-{stamp}-{uuid4().hex[:8]}.sqlite3'
    started = monotonic()
    last_report = [float('-inf')]

    def emit(message):
        if notify is not None:
            notify(message)

    def progress(status, remaining, total):
        now = monotonic()
        if status != SQLITE_DONE and now - last_report[0] < 5:
            return
        last_report[0] = now
        if status in (SQLITE_BUSY, SQLITE_LOCKED):
            emit(f'Backup waiting for SQLite lock; elapsed {now - started:.0f}s')
        else:
            percent = 100 * (total - remaining) / total if total else 100
            emit(f'Backup copy: {percent:.1f}% ({total - remaining}/{total} pages); elapsed {now - started:.0f}s')

    def checking():
        now = monotonic()
        if now - last_report[0] >= 5:
            last_report[0] = now
            emit(f'Backup integrity check running; elapsed {now - started:.0f}s')
        return 0

    emit(f'Backup destination: {destination}')
    try:
        with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)) as source:
            with closing(sqlite3.connect(destination)) as target:
                if notify is None:
                    source.backup(target)
                else:
                    source.backup(target, pages=256, progress=progress)
                emit('Backup copy complete. Starting integrity check (this reads the whole copy).')
                if notify is not None:
                    target.set_progress_handler(checking, 100000)
                try:
                    result = target.execute('PRAGMA quick_check').fetchone()[0]
                finally:
                    if notify is not None:
                        target.set_progress_handler(None, 0)
                if result != 'ok':
                    raise RuntimeError(f'Проверка резервной копии: {result}')
    except Exception:
        try:
            destination.unlink(missing_ok=True)
        except OSError:
            logging.getLogger(__name__).warning('Не удалось удалить неполную резервную копию: %s', destination)
        raise
    emit(f'Backup verified; elapsed {monotonic() - started:.1f}s')
    return destination
