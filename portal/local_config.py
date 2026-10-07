"""Resolve the same persistent SQLite location for every local entry point."""
import json
import os
from pathlib import Path


def database_path(base_dir):
    configured = os.environ.get('DJANGO_DB_PATH')
    if not configured:
        config_file = Path(base_dir) / '.local' / 'config.json'
        if config_file.exists():
            try:
                configured = json.loads(config_file.read_text(encoding='utf-8')).get('database_path')
            except (OSError, ValueError) as error:
                raise RuntimeError(f'Не удалось прочитать {config_file}: {error}') from error
    path = Path(configured).expanduser() if configured else Path(base_dir) / 'db.sqlite3'
    if not path.is_absolute():
        path = Path(base_dir) / path
    return path.resolve()
