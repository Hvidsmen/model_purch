"""SQL Server connections shared by the portal modules."""
from urllib.parse import quote_plus

from django.conf import settings
import pyodbc
from sqlalchemy import create_engine as sqlalchemy_create_engine


def connection_string(server, database):
    setting = 'MS_SQL_CONN_STR' if database.lower() == 'modelpurch' else 'DWH_SQL_CONN_STR'
    configured = getattr(settings, setting, None)
    if configured is not None and not isinstance(configured, str):
        raise RuntimeError(f'{setting} должна быть строкой подключения ODBC.')
    if configured and configured.strip():
        return configured
    if settings.SETTINGS_MODULE == 'portal.settings_local':
        raise RuntimeError(
            f'Не настроено подключение к SQL Server: задайте {setting} в PowerShell '
            'перед запуском сервера и перезапустите Django. '
            'Локальные настройки без этой переменной не выполняют корпоративные расчёты.'
        )
    return f'DRIVER={{SQL Server}};SERVER={server};DATABASE={database};Trusted_Connection=yes;'


def connect_database(server, database):
    connection = pyodbc.connect(connection_string(server, database))
    return connection, connection.cursor()


def create_engine(server, database):
    return sqlalchemy_create_engine(
        'mssql+pyodbc:///?odbc_connect=' + quote_plus(connection_string(server, database))
    )
