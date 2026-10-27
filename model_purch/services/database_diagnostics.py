"""Read-only, time-bounded SQLite storage diagnostics; never vacuum or delete data."""
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import shutil
import sqlite3
import time


def diagnose_database(database, include_tables=False, max_seconds=15, backup_directory=None):
    path = Path(database).resolve()
    if not path.is_file():
        raise FileNotFoundError('Файл базы данных не найден.')
    def file_bytes(filename):
        try:
            return Path(filename).stat().st_size
        except FileNotFoundError:
            return 0
    result = {'generated_at': datetime.now(timezone.utc).isoformat(), 'engine': 'SQLite',
              'database_bytes': path.stat().st_size,
              'wal_bytes': file_bytes(str(path) + '-wal'),
              'disk_free_bytes': shutil.disk_usage(path.parent).free,
              'recommendations': [], 'objects': []}
    if backup_directory is not None:
        backup_path = Path(backup_directory).resolve()
        existing_parent = backup_path
        while not existing_parent.exists() and existing_parent != existing_parent.parent:
            existing_parent = existing_parent.parent
        result['backup_disk_free_bytes'] = shutil.disk_usage(existing_parent).free
        result['backup_on_same_filesystem'] = path.stat().st_dev == existing_parent.stat().st_dev
        if result['backup_disk_free_bytes'] < result['database_bytes'] * 2:
            result['recommendations'].append('На диске резервных копий мало свободного места. Освободите место или перенесите резервирование на подходящий диск; отчёт сам ничего не перемещает.')
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=2)) as connection:
        connection.execute('PRAGMA query_only=ON')
        deadline = time.monotonic() + max_seconds
        connection.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
        try:
            for name in ('page_size', 'page_count', 'freelist_count', 'journal_mode'):
                result[name] = connection.execute('PRAGMA ' + name).fetchone()[0]
            result['free_inside_database_bytes'] = result['page_size'] * result['freelist_count']
            if include_tables:
                try:
                    rows = connection.execute('''SELECT s.name, COALESCE(m.tbl_name,s.name),
                        COALESCE(m.type,'internal'), SUM(s.pgsize), COUNT(*)
                        FROM dbstat AS s LEFT JOIN sqlite_schema AS m ON m.name=s.name
                        GROUP BY s.name ORDER BY SUM(s.pgsize) DESC LIMIT 30''').fetchall()
                    result['objects'] = [dict(zip(('name', 'table', 'type', 'bytes', 'pages'), row)) for row in rows]
                    result['table_details'] = 'complete'
                except sqlite3.OperationalError:
                    result['table_details'] = 'unavailable_or_time_limit'
                    result['recommendations'].append('Разбивка по таблицам недоступна или превысила лимит времени. Повторите команду database_diagnostics --tables --timeout 120 в спокойное время.')
        finally:
            connection.set_progress_handler(None, 0)
    if result['disk_free_bytes'] < result['database_bytes'] * 2:
        result['recommendations'].append('На диске мало места для резервирования и обслуживания: желательно иметь свободное место не меньше двух размеров базы. Проверьте диск до следующего обновления.')
    if result['free_inside_database_bytes'] > result['page_size'] * result['page_count'] * .2:
        result['recommendations'].append('Более 20% страниц свободны внутри файла. После проверенной резервной копии можно запланировать отдельное обслуживание VACUUM; автоматически оно не запускается.')
    result['recommendations'].extend([
        'Сопоставьте крупнейшие таблицы с нужной глубиной истории. Удаление данных требует отдельного решения; этот отчёт ничего не удаляет.',
        'Размещайте рабочую базу и резервные копии на быстрых локальных дисках. Тип SSD/HDD проверяется средствами Windows; по имени диска его определить нельзя.'
    ])
    return result
