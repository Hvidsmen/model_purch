"""Persist local database selection and prepare the environment before runserver."""
import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--database')
    options = parser.parse_args()
    from portal.local_config import database_path
    selected = Path(options.database).expanduser() if options.database else database_path(ROOT)
    if not selected.is_absolute():
        selected = ROOT / selected
    selected = selected.resolve()
    if options.database and not selected.is_file():
        parser.error(f'Указанная база не существует: {selected}. Проверьте путь.')
    if selected.exists():
        with sqlite3.connect(selected.as_uri() + '?mode=ro', uri=True) as database:
            if database.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                raise RuntimeError('SQLite не прошла проверку целостности.')
    selected.parent.mkdir(parents=True, exist_ok=True)
    os.environ['DJANGO_DB_PATH'] = str(selected)
    os.environ['DJANGO_SETTINGS_MODULE'] = 'portal.settings_local'
    import django
    django.setup()
    from django.core.management import call_command
    call_command('check')
    call_command('migrate', interactive=False)
    from model_purch.models import ScenarioModel
    print(f'Активная база: {selected}')
    print(f'Сценариев: {ScenarioModel.objects.count()}')
    config_file = ROOT / '.local' / 'config.json'
    config_file.parent.mkdir(parents=True, exist_ok=True)
    temporary = config_file.with_suffix('.tmp')
    temporary.write_text(json.dumps({'database_path': str(selected)}, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(config_file)


if __name__ == '__main__':
    main()
