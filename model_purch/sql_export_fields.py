"""Additive schema updates and export of newly introduced model fields."""
import json
from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import UUID

from django.db import models


def identifier(name):
    return '[' + name.replace(']', ']]') + ']'


def sql_type(field):
    if isinstance(field, models.BooleanField):
        return 'BIT'
    if isinstance(field, models.BigIntegerField):
        return 'BIGINT'
    if isinstance(field, models.IntegerField):
        return 'INT'
    if isinstance(field, models.FloatField):
        return 'FLOAT'
    if isinstance(field, models.DecimalField):
        return f'DECIMAL({field.max_digits}, {field.decimal_places})'
    if isinstance(field, models.DateTimeField):
        return 'DATETIME2'
    if isinstance(field, models.DateField):
        return 'DATE'
    if isinstance(field, models.TimeField):
        return 'TIME'
    if isinstance(field, models.UUIDField):
        return 'UNIQUEIDENTIFIER'
    if isinstance(field, (models.JSONField, models.TextField)):
        return 'NVARCHAR(MAX)'
    if isinstance(field, (models.CharField, models.FileField)):
        length = field.max_length
        return f'NVARCHAR({length})' if length and length <= 4000 else 'NVARCHAR(MAX)'
    raise ValueError(f'Нет SQL-экспорта для поля {field.model.__name__}.{field.name}: {field.get_internal_type()}')


def scalar_fields(model):
    return [field for field in model._meta.concrete_fields if not field.primary_key and not field.is_relation]


def ensure_column(cursor, table, name, column_type):
    # Table/column names originate only from model metadata and fixed export mappings.
    cursor.execute(f"""
        IF COL_LENGTH(N'portal.{table}', N'{name}') IS NULL
            ALTER TABLE [portal].{identifier(table)} ADD {identifier(name)} {column_type} NULL;
    """)


def ensure_model_columns(cursor, table, model, supported_relations):
    unknown = [field.name for field in model._meta.concrete_fields
               if field.is_relation and field.name not in supported_relations]
    if unknown:
        raise ValueError(f'Нужно настроить экспорт связей {model.__name__}: {", ".join(unknown)}')
    for field in scalar_fields(model):
        ensure_column(cursor, table, field.name, sql_type(field))


def odbc_value(field, value):
    if value is None:
        return None
    if isinstance(field, models.JSONField):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            value = value.astimezone(timezone.utc).replace(tzinfo=None)
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (Decimal, UUID)):
        return str(value)
    if isinstance(field, models.FileField):
        return value.name
    return value


def export_additional_fields(cursor, table, instance, handled_fields, identity):
    fields = [field for field in scalar_fields(type(instance)) if field.name not in handled_fields]
    if not fields:
        return
    assignments = ', '.join(f'{identifier(field.name)} = ?' for field in fields)
    condition = ' AND '.join(f'{identifier(name)} = ?' for name in identity)
    parameters = [odbc_value(field, getattr(instance, field.attname)) for field in fields]
    cursor.execute(f'UPDATE [portal].{identifier(table)} SET {assignments} WHERE {condition}',
                   *parameters, *identity.values())
