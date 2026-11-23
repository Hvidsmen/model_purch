"""Explicit configuration for an isolated SQL Server rehearsal, never DataWH."""
import os
import re
from pathlib import Path
from django.core.exceptions import ImproperlyConfigured


def trial_name(name):
    if not re.fullmatch(r'Portal_Test(?:_[A-Za-z0-9]+)?',name or '',re.IGNORECASE):
        raise ImproperlyConfigured('Тестовая база должна называться Portal_Test или Portal_Test_<суффикс>.')
    return name


def database_config(env=None):
    env=os.environ if env is None else env
    name=trial_name(env.get('PORTAL_TRIAL_DATABASE','Portal_Test'))
    host=env.get('PORTAL_TRIAL_SERVER','').strip()
    if not host or any(char in host for char in ';\r\n'):
        raise ImproperlyConfigured('Задайте PORTAL_TRIAL_SERVER, например vm-dwh.')
    driver=env.get('PORTAL_TRIAL_DRIVER','ODBC Driver 17 for SQL Server')
    if driver not in ('ODBC Driver 17 for SQL Server','ODBC Driver 18 for SQL Server'):
        raise ImproperlyConfigured('Для теста нужен Microsoft ODBC Driver 17 или 18.')
    trust='yes' if env.get('PORTAL_TRIAL_TRUST_CERTIFICATE','').lower() in ('1','true','yes') else 'no'
    return {'ENGINE':'mssql','NAME':name,'HOST':host,'PORT':env.get('PORTAL_TRIAL_PORT',''),
            'USER':env.get('PORTAL_TRIAL_USER',''),'PASSWORD':env.get('PORTAL_TRIAL_PASSWORD',''),
            'CONN_MAX_AGE':0,
            'OPTIONS':{'driver':driver,'connection_timeout':10,'connection_retries':0,
                       'query_timeout':120,'collation':'Latin1_General_100_CI_AS','extra_params':f'Encrypt=yes;TrustServerCertificate={trust}'},
            'TEST':{'NAME':name+'_Unit'}}


def source_config(env=None):
    env=os.environ if env is None else env
    value=env.get('PORTAL_TRIAL_SQLITE_SNAPSHOT','')
    path=Path(value).expanduser().resolve()
    if not value or not path.is_file():
        raise ImproperlyConfigured('PORTAL_TRIAL_SQLITE_SNAPSHOT должен указывать на подготовленную копию SQLite.')
    return {'ENGINE':'django.db.backends.sqlite3','NAME':path.as_uri()+'?mode=ro',
            'OPTIONS':{'uri':True,'timeout':30}}


def redact(value):
    value=str(value)
    # Mask the rest of a connection-string line, including escaped ODBC braces.
    value=re.sub(r'(?i)\b(?:password|pwd|uid|user\s*id|username|access[_ ]?token|client[_ ]?secret|driver|server)\s*=[^\r\n]*',
                 '[параметры подключения скрыты]',value)
    value=re.sub(r"""(?i)login failed for user\s*['"][^'"]*['"]""","Login failed for user [скрыто]",value)
    return value
