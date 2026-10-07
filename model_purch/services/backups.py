"""Consistent SQLite snapshots, including committed WAL data."""
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


def backup_sqlite(database, directory):
    database = Path(database).resolve()
    if not database.is_file():
        raise FileNotFoundError(f'База не найдена: {database}')
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    destination = directory / f'{database.stem}-{stamp}-{uuid4().hex[:8]}.sqlite3'
    try:
        with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True) as source:
            with sqlite3.connect(destination) as target:
                source.backup(target)
                result = target.execute('PRAGMA quick_check').fetchone()[0]
                if result != 'ok':
                    raise RuntimeError(f'Проверка резервной копии: {result}')
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    return destination
