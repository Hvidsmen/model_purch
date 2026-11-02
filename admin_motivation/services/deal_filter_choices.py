"""Faceted deal filters, bounded to the same identity and source as report rows."""
from .reports import FILTERS, bind_sql, fetch
from ..conns import connect_database

COLUMNS = {'type': 'ТипКоэф', 'subdivision': 'Подразделение', 'channel': 'Канал',
           'author': 'АвторСделки', 'planning': 'ГруппаПлановПродаж', 'goods': 'ГруппаТоваров',
           'brand': 'Марка', 'partner': 'Партнер'}


def choices(kind, values, refresh=False, access_scope=None):
    from base.report_access import scoped_values, worker_scope, identity_choices
    from .subdivision_scope import sql_predicate
    scope = worker_scope(access_scope, kind, values) if access_scope else None
    if scope:
        values = scoped_values(kind, values, scope)
    identity = identity_choices(kind, values, scope) if scope else {}
    result, connection = [], None
    try:
        for name, parameter, caption, _, multiple in FILTERS['deals']:
            selected = values.get(parameter)
            selected = selected if isinstance(selected, list) else [selected] if selected is not None else []
            item = {'name': name, 'label': caption, 'multiple': multiple}
            if name in identity:
                result.append({**item, **identity[name]})
                continue
            if name not in ('type', 'subdivision') and (not values.get('Division') or not values.get('TypeCoeff')):
                result.append({**item, 'options': []})
                continue
            predicates = [sql_predicate('[Подразделение]'), '[ДатаЗакрытияСделки] BETWEEN @StartDate AND @EndDate']
            for other, key, _, _, _ in FILTERS['deals']:
                if name in ('type', 'subdivision') and other not in ('type', 'subdivision'):
                    continue  # A previous manager/product must not trap the primary selectors.
                # Scope constraints never disappear when requesting options.
                if other == name and not (scope and other in ('type', 'subdivision', 'author')):
                    continue
                if values.get(key):
                    predicates.append(f'[{COLUMNS[other]}] IN (@{key})')
            column = f'LTRIM(RTRIM([{COLUMNS[name]}]))' if name == 'type' else f'[{COLUMNS[name]}]'
            sql, args = bind_sql(f'SELECT DISTINCT {column} AS value FROM DataWH.motivation.Fact WHERE ' + ' AND '.join(predicates), values)
            if connection is None:
                connection, cursor = connect_database('vm-dwh', 'DataWH')
                connection.timeout = 30
            options = {str(row['value']) for row in fetch(cursor, sql, args, limit=10000) if row.get('value') is not None and str(row['value']).strip()}
            options.update(str(value) for value in selected if str(value).strip())
            result.append({**item, 'options': [{'value': value, 'selected': value in selected} for value in sorted(options)]})
        return result
    finally:
        if connection is not None:
            connection.close()
