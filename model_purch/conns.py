"""SQL Server connections shared by the portal modules."""
from urllib.parse import quote_plus

from django.conf import settings
import pyodbc
from sqlalchemy import create_engine as sqlalchemy_create_engine


def connection_string(server, database):
    setting = 'MS_SQL_CONN_STR' if database.lower() == 'modelpurch' else 'DWH_SQL_CONN_STR'
    configured = getattr(settings, setting, None)
    if configured:
        return configured
    if settings.SETTINGS_MODULE == 'portal.settings_local':
        raise RuntimeError(f'Set {setting} to enable corporate SQL Server operations.')
    return f'DRIVER={{SQL Server}};SERVER={server};DATABASE={database};Trusted_Connection=yes;'


def connect_database(server, database):
    connection = pyodbc.connect(connection_string(server, database))
    return connection, connection.cursor()


def create_engine(server, database):
    return sqlalchemy_create_engine(
        'mssql+pyodbc:///?odbc_connect=' + quote_plus(connection_string(server, database))
    )
