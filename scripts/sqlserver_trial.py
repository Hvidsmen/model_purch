"""Rehearse a portal SQLite -> SQL Server move without changing production configuration."""
import argparse
from contextlib import closing
from datetime import datetime,timezone
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import uuid

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))


def backup_sqlite(source,destination):
    source=Path(source).resolve();destination=Path(destination).resolve()
    if not source.is_file():raise RuntimeError('Исходный файл SQLite не найден.')
    if source==destination:raise RuntimeError('Копия SQLite должна иметь другой путь.')
    destination.parent.mkdir(parents=True,exist_ok=True)
    if destination.exists():raise RuntimeError('Файл снимка уже существует; перезапись запрещена.')
    with closing(sqlite3.connect(source.as_uri()+'?mode=ro',uri=True)) as live:
        with closing(sqlite3.connect(destination)) as snapshot:
            live.backup(snapshot,pages=512,sleep=.01)
            if snapshot.execute('PRAGMA quick_check').fetchone()[0]!='ok':
                raise RuntimeError('Копия SQLite не прошла проверку целостности.')
    try:destination.chmod(0o600)
    except OSError:pass


def execute(command,env):
    from portal.sqlserver_trial_config import redact
    process=subprocess.Popen(command,cwd=ROOT,env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,encoding='utf-8',errors='replace')
    for line in process.stdout:print(redact(line).rstrip(),flush=True)
    code=process.wait()
    if code:raise subprocess.CalledProcessError(code,command)


def managed(env,*args):
    execute([sys.executable,str(ROOT/'manage.py'),*args],env)


def migrate_snapshot(env):
    # Bypass the project's production backup-retention wrapper ONLY for this disposable copy.
    # An immutable source-original.sqlite3 already exists in the private trial directory.
    code="import django; django.setup(); from django.core.management.commands.migrate import Command; Command().run_from_argv(['manage.py','migrate','--noinput','--verbosity=0'])"
    execute([sys.executable,'-c',code],env)


def odbc(value):
    return '{'+str(value).replace('}','}}')+'}'


def prepare_target(config,run_id):
    import pyodbc
    driver=config['OPTIONS']['driver']
    if driver not in pyodbc.drivers():raise RuntimeError('Не установлен '+driver+'. Установите Microsoft ODBC Driver на сервере портала.')
    host=config['HOST']+(','+config['PORT'] if config.get('PORT') else '')
    parts=['DRIVER='+odbc(driver),'SERVER='+odbc(host),'DATABASE=master',config['OPTIONS']['extra_params']]
    if config.get('USER'):parts+=['UID='+odbc(config['USER']),'PWD='+odbc(config.get('PASSWORD',''))]
    else:parts+=['Trusted_Connection=yes']
    connection=pyodbc.connect(';'.join(parts),autocommit=True,timeout=10)
    try:
        cursor=connection.cursor()
        cursor.execute("SELECT CAST(SERVERPROPERTY('ProductMajorVersion') AS int)")
        major=int(cursor.fetchone()[0])
        if major<14:raise RuntimeError('Для теста требуется SQL Server 2017 или новее.')
        name=config['NAME'];quoted='['+name.replace(']',']]')+']'
        cursor.execute('SELECT DB_ID(?)',name)
        exists=cursor.fetchone()[0] is not None
        if not exists:cursor.execute('CREATE DATABASE '+quoted+' COLLATE Latin1_General_100_BIN2')
        cursor.execute('USE '+quoted)
        cursor.execute('SELECT compatibility_level FROM sys.databases WHERE name=DB_NAME()')
        if int(cursor.fetchone()[0])<130:raise RuntimeError('Для JSON требуется compatibility_level не ниже 130. Настройки существующей базы не изменены.')
        cursor.execute('SELECT COUNT(*) FROM sys.tables WHERE is_ms_shipped=0')
        if cursor.fetchone()[0]:
            raise RuntimeError('Тестовая база уже содержит таблицы. Существующие данные не очищены. Используйте новую пустую Portal_Test_<суффикс>.')
        cursor.execute("SELECT COUNT(*) FROM sys.extended_properties WHERE class=0 AND name=N'PortalSqliteTrialRun'")
        if cursor.fetchone()[0]:raise RuntimeError('База уже относится к другому тестовому запуску.')
        cursor.execute("EXEC sys.sp_addextendedproperty @name=N'PortalSqliteTrialRun', @value=?",run_id)
        cursor.execute("EXEC sys.sp_addextendedproperty @name=N'PortalSqliteTrialPhase', @value=N'new'")
    finally:connection.close()


def trial_environment(workspace,metadata):
    env=os.environ.copy()
    env.update(DJANGO_SETTINGS_MODULE='portal.settings_sqlserver_trial',
        PORTAL_TRIAL_SERVER=metadata['server'],PORTAL_TRIAL_DATABASE=metadata['database'],
        PORTAL_TRIAL_SQLITE_SNAPSHOT=str(workspace/'source.sqlite3'),PORTAL_TRIAL_RUN_ID=metadata['run_id'])
    return env


def prepare_snapshot(workspace,source):
    print('1. Согласованная копия SQLite…',flush=True)
    print('Источник (только чтение): '+str(Path(source).resolve()),flush=True)
    backup_sqlite(source,workspace/'source-original.sqlite3')
    backup_sqlite(workspace/'source-original.sqlite3',workspace/'source.sqlite3')
    env=os.environ.copy()
    env.update(DJANGO_SETTINGS_MODULE='portal.settings_local',DJANGO_DB_PATH=str(workspace/'source.sqlite3'))
    print('2. Миграции только на копии SQLite…',flush=True)
    migrate_snapshot(env)
    print('3. Выгрузка данных с исходными идентификаторами…',flush=True)
    managed(env,'dumpdata','--settings=portal.settings_local','--all','--exclude=sessions.session',
            '--output='+str(workspace/'data.json'),'--verbosity=0')
    try:(workspace/'data.json').chmod(0o600)
    except OSError:pass


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--server',default='vm-dwh')
    parser.add_argument('--database',default='Portal_Test')
    parser.add_argument('--source')
    parser.add_argument('--workspace')
    parser.add_argument('--snapshot-only',action='store_true')
    parser.add_argument('--run-prepared',action='store_true')
    parser.add_argument('--serve',action='store_true')
    parser.add_argument('--port',type=int,default=8010)
    options=parser.parse_args()
    from portal.local_config import database_path
    if not options.source:options.source=str(database_path(ROOT))
    from portal.sqlserver_trial_config import database_config,trial_name
    trial_name(options.database)
    base=ROOT/'.local'/'sqlserver-trial'
    if options.run_prepared:
        workspace=Path(options.workspace).resolve() if options.workspace else Path(json.loads((base/'latest.json').read_text())['workspace'])
        metadata=json.loads((workspace/'run.json').read_text(encoding='utf-8'))
        if not (workspace/'verified.json').exists() or not json.loads((workspace/'verified.json').read_text())['passed']:
            raise RuntimeError('Нет успешного отчёта сверки.')
        if not (workspace/'probe.json').exists() or not json.loads((workspace/'probe.json').read_text())['passed']:
            raise RuntimeError('Нет успешной проверки совместимости SQL Server.')
        env=trial_environment(workspace,metadata)
    else:
        workspace=Path(options.workspace).resolve() if options.workspace else base/(datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:6])
        if workspace.exists():raise RuntimeError('Рабочая папка тестового запуска уже существует; перезапись запрещена.')
        workspace.mkdir(parents=True)
        try:workspace.chmod(0o700)
        except OSError:pass
        metadata={'server':options.server,'database':options.database,'run_id':uuid.uuid4().hex,
                  'created_at_utc':datetime.now(timezone.utc).isoformat(),'workspace':str(workspace),
                  'source_sqlite':str(Path(options.source).resolve())}
        (workspace/'run.json').write_text(json.dumps(metadata,ensure_ascii=False,indent=2),encoding='utf-8')
        prepare_snapshot(workspace,options.source)
        if options.snapshot_only:
            print('Копия и выгрузка готовы. SQL Server не затронут: '+str(workspace),flush=True);return
        env=trial_environment(workspace,metadata)
        if importlib.util.find_spec('mssql') is None:raise RuntimeError('Установите requirements-sqlserver-trial.txt в отдельное окружение.')
        config=database_config(env)
        print('4. Отдельная пустая база SQL Server: '+metadata['database']+'…',flush=True)
        prepare_target(config,metadata['run_id'])
        print('5. Миграции SQL Server…',flush=True)
        managed(env,'migrate','--noinput','--verbosity=1')
        managed(env,'sqlserver_trial_data','clear-seeds')
        print('6. Загрузка данных…',flush=True)
        managed(env,'loaddata',str(workspace/'data.json'),'--verbosity=1')
        managed(env,'sqlserver_trial_data','reseed')
        print('7. Сверка всех таблиц, связей и JSON…',flush=True)
        managed(env,'sqlserver_trial_data','verify','--report='+str(workspace/'verified.json'))
        print('8. Проверка JSON-фильтров, расчётных типов и блокировок…',flush=True)
        managed(env,'sqlserver_trial_data','probe','--report='+str(workspace/'probe.json'))
        managed(env,'collectstatic','--noinput','--verbosity=0')
        (base/'latest.json').write_text(json.dumps({'workspace':str(workspace)},ensure_ascii=False),encoding='utf-8')
        print('Тестовый перенос завершён. Отчёты: '+str(workspace),flush=True)
    if options.serve or options.run_prepared:
        if not 1024<=options.port<=65535:raise RuntimeError('Порт должен быть от 1024 до 65535.')
        managed(env,'sqlserver_trial_data','identity')
        print(f'Только тестовый портал: http://127.0.0.1:{options.port}/ . Рабочий портал не переключён.',flush=True)
        managed(env,'runserver',f'127.0.0.1:{options.port}','--noreload','--insecure')


if __name__=='__main__':
    try:main()
    except subprocess.CalledProcessError as error:
        print('Этап переноса завершился ошибкой. Рабочая база не переключена. Код: '+str(error.returncode),file=sys.stderr)
        sys.exit(error.returncode or 1)
    except Exception as error:
        # Never print connection strings or fixture record representations.
        from portal.sqlserver_trial_config import redact
        print(redact(error)[:1500],file=sys.stderr);sys.exit(1)
