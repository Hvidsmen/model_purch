"""Persist local database selection and prepare the environment before runserver."""
import argparse
import json
import os
import sqlite3
from contextlib import closing
import sys
from time import perf_counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def stage(title, action):
    print(f'{title}…', flush=True)
    started = perf_counter()
    result = action()
    print(f'{title}: готово за {perf_counter() - started:.1f} сек.', flush=True)
    return result


def check_database(selected):
    with closing(sqlite3.connect(selected.as_uri() + '?mode=ro', uri=True)) as database:
        if database.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
            raise RuntimeError('SQLite не прошла проверку целостности.')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--database')
    parser.add_argument('--check-database', action='store_true', help='Выполнить полную проверку SQLite перед запуском.')
    options = parser.parse_args()
    from portal.local_config import database_path
    selected = Path(options.database).expanduser() if options.database else database_path(ROOT)
    if not selected.is_absolute():
        selected = ROOT / selected
    selected = selected.resolve()
    if options.database and not selected.is_file():
        parser.error(f'Указанная база не существует: {selected}. Проверьте путь.')
    print(f'Подготовка портала. База: {selected}', flush=True)
    if options.check_database and selected.exists():
        stage('Полная проверка базы SQLite', lambda: check_database(selected))
    selected.parent.mkdir(parents=True, exist_ok=True)
    os.environ['DJANGO_DB_PATH'] = str(selected)
    os.environ['DJANGO_SETTINGS_MODULE'] = 'portal.settings_local'
    import django
    stage('Загрузка Django', django.setup)
    from django.core.management import call_command
    stage('Проверка конфигурации', lambda: call_command('check'))
    stage('Проверка и применение миграций', lambda: call_command('migrate', interactive=False))
    stage('Обновление статических файлов', lambda: call_command('collectstatic', interactive=False, verbosity=0))
    from model_purch.models import ScenarioModel
    print(f'Активная база: {selected}', flush=True)
    print(f'Сценариев: {ScenarioModel.objects.count()}', flush=True)
    from django.conf import settings
    configured = bool(getattr(settings, 'MS_SQL_CONN_STR', None))
    print('MS SQL: строка подключения загружена' if configured else 'MS SQL: строка подключения не задана; экспорт и расчёт недоступны')
    config_file = ROOT / '.local' / 'config.json'
    config_file.parent.mkdir(parents=True, exist_ok=True)
    temporary = config_file.with_suffix('.tmp')
    temporary.write_text(json.dumps({'database_path': str(selected)}, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(config_file)


if __name__ == '__main__':
    main()
