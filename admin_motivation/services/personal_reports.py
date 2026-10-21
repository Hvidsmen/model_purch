"""Lookups for personal reports, bounded to the same identity as report rows."""
from ..conns import connect_database
from .reports import FILTERS, bind_sql, fetch


def choices(kind, values, scope):
    from base.report_access import identity_choices
    identity = identity_choices(kind, values, scope)
    columns = {'planning': 'ГруппаПлановПродаж', 'goods': 'ГруппаТоваров', 'brand': 'Марка',
               'year': 'Год', 'quarter': 'КварталМотивации', 'channel': 'Канал', 'partner': 'Партнер'}
    if kind == 'deals':
        source = ('DataWH.motivation.Fact WHERE [Подразделение] IN (@Division) '
                  'AND [АвторСделки] IN (@ManagerName) AND [ТипКоэф]=@TypeCoeff '
                  'AND [ДатаЗакрытияСделки] BETWEEN @StartDate AND @EndDate')
    else:
        source = ('DataWH.motivation.ReportMotivationByDealAuthor WHERE [Подразделение] IN (@Subdivision) '
                  'AND [АвторСделки] IN (@DealAuthor) AND [ТипКоэф]=@TypeCoeff_')
    connection = None
    try:
        connection, cursor = connect_database('vm-dwh', 'DataWH')
        connection.timeout = 30
        result = []
        for name, parameter, caption, dataset, multiple in FILTERS[kind]:
            selected = values.get(parameter)
            selected = selected if isinstance(selected, list) else [selected] if selected is not None else []
            if name in identity:
                result.append({'name': name, 'label': caption, 'multiple': multiple, **identity[name]})
                continue
            else:
                column = columns[name]
                sql, args = bind_sql('SELECT DISTINCT [' + column + '] AS value FROM ' + source, values)
                options = sorted({str(row['value']) for row in fetch(cursor, sql, args, limit=10000) if row.get('value') is not None})
            result.append({'name': name, 'label': caption, 'multiple': multiple, 'locked': False,
                'locked_value': None, 'options': [{'value': value, 'selected': value in selected} for value in options]})
        return result
    finally:
        if connection is not None:
            connection.close()
