"""Native Django reports using read-only, parameterized datasets from the supplied RDLs."""
from collections import defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation
import json
import re
from pathlib import Path
from django.core.exceptions import ValidationError
from django.core.cache import cache
from django.conf import settings
import hashlib
from ..conns import connect_database

SQL_ROOT = Path(__file__).resolve().parents[1] / 'sql' / 'reports'
CATALOG = {
    'manager-detail': {'title': 'Мотивация. Детально', 'description': 'Подробный отчёт: подразделения, сотрудники, кварталы, коэффициенты и товарная матрица. USD и RUB.'},
    'manager': {'title': 'Мотивация. Дашборд', 'description': 'Квартал и год, выполнение плана, доступная мотивация и доплата за кварталы.'},
    'deals': {'title': 'Факт закрытых сделок', 'description': 'Отгрузки и возможная мотивация по сделкам, менеджерам и товарной матрице.'},
    'subdivisions': {'title': 'Мотивация по подразделениям', 'description': 'Итоги по филиалам и раскрываемая детализация товарной матрицы.'},
}
# UI name, RDL parameter, caption, lookup dataset, multiple
FILTERS = {
 'manager-detail': [('type', 'TypeCoeff_', 'Вид работника', 'TPK', False), ('subdivision', 'Subdivision', 'Подразделение', 'Sub', True),
                    ('author', 'DealAuthor', 'Автор сделки', 'DA', True), ('planning', 'gp_sales', 'Группа планов продаж', 'PGSales', True),
                    ('goods', 'GPGoods', 'Группа товаров', 'GPGoods', True), ('brand', 'Brend', 'Марка / бренд', 'Brend', True),
                    ('year', 'Years', 'Год', 'Years', True)],
 'manager': [('type', 'TypeCoeff_', 'Вид работника', 'TPK', False), ('subdivision', 'Subdivision', 'Подразделение', 'Sub', False),
             ('author', 'DealAuthor', 'Автор сделки', 'DA', False), ('planning', 'gp_sales', 'Группа планов продаж', 'PGSales', True),
             ('quarter', 'qm', 'Квартал', 'Quar', False)],
 'deals': [('type', 'TypeCoeff', 'Тип коэффициента', 'TC', False), ('subdivision', 'Division', 'Подразделение', 'Sub', False),
           ('channel', 'Channel', 'Канал', 'Chanel', True), ('author', 'ManagerName', 'Менеджер', 'DataSet1', True),
           ('planning', 'GPSales', 'Группа планов продаж', 'GPSales', True), ('goods', 'GPGoods', 'Группа товаров', 'GPGoods', True),
           ('brand', 'Brend', 'Марка / бренд', 'Brend', True), ('partner', 'Partner', 'Партнёр', 'Partner', True)],
 'subdivisions': [('type', 'type_coeff', 'Тип коэффициента', 'TypeCoeff', True), ('subdivision', 'Subdivision', 'Подразделение', 'Sub', True),
                  ('planning', 'gp_sales', 'Группа планов продаж', 'GPsales', True), ('goods', 'GPGoods', 'Группа товаров', 'GPGoods', True),
                  ('brand', 'Brend', 'Марка / бренд', 'Brend', True)],
}


def parameters(kind, query):
    values = {}
    for name, parameter, _, _, multi in FILTERS[kind]:
        items = query.getlist(name) if hasattr(query, 'getlist') else query.get(name, [])
        if isinstance(items, str): items = [items]
        if len(items) > 100 or any(len(value) > 255 for value in items):
            raise ValidationError('Слишком много значений фильтра или слишком длинное значение.')
        values[parameter] = list(dict.fromkeys(items)) if multi else (items[0] if items else None)
    if kind in ('manager', 'manager-detail'):
        currency = query.get('currency', 'USD')
        if currency not in ('USD', 'RUB'):
            raise ValidationError('Выберите валюту USD или RUB.')
        values['Currency'] = currency
    if kind == 'deals':
        try:
            start, end = date.fromisoformat(query.get('start', '')), date.fromisoformat(query.get('end', ''))
        except (ValueError, TypeError):
            raise ValidationError('Укажите даты начала и окончания периода.')
        if end < start: raise ValidationError('Дата окончания раньше даты начала.')
        values.update(StartDate=start, EndDate=end)
    return values


def bind_sql(sql, parameters):
    """Expand lists without inserting values into SQL; empty optional lists mean all."""
    bound = []
    token = re.compile(r"(?P<column>COALESCE\([^\n]*?\)|[\w\[\].]+)\s+IN\s*\(\s*@(?P<list>\w+)\s*\)|@(?P<scalar>\w+)", re.I)
    def replace(match):
        key = match.group('list') or match.group('scalar')
        if key not in parameters: raise ValidationError('Не задан параметр запроса: ' + key)
        value = parameters[key]
        if match.group('list'):
            items = value if isinstance(value, list) else ([value] if value is not None else [])
            if not items: return '1=1'
            bound.extend(items)
            return match.group('column') + ' IN (' + ','.join('?' for _ in items) + ')'
        bound.append(value)
        return '?'
    return token.sub(replace, sql), bound


def fetch(cursor, sql, args=(), limit=50000):
    cursor.execute(sql, *args)
    columns = [item[0] for item in cursor.description]
    rows = cursor.fetchmany(limit + 1)
    if len(rows) > limit: raise ValidationError('Слишком много строк. Уточните фильтры отчёта.')
    return [dict(zip(columns, row)) for row in rows]


def choices(kind, values, refresh=False):
    queries = json.loads((SQL_ROOT / 'lookups.json').read_text(encoding='utf-8'))[kind]
    connection = None
    signature = hashlib.sha256(str(getattr(settings, 'DWH_SQL_CONN_STR', '')).encode()).hexdigest()[:16]
    try:
        connection, cursor = connect_database('vm-dwh', 'DataWH')
        connection.timeout = 30
        result = []
        for name, parameter, caption, dataset, multiple in FILTERS[kind]:
            if kind in ('manager', 'manager-detail') and dataset == 'DA' and (not values.get('TypeCoeff_') or (kind == 'manager' and not values.get('Subdivision'))):
                result.append({'name': name, 'label': caption, 'multiple': multiple, 'options': []})
                continue
            if kind == 'deals' and dataset not in ('Sub', 'TC') and not values.get('Division'):
                result.append({'name': name, 'label': caption, 'multiple': multiple, 'options': []})
                continue
            sql, args = bind_sql(queries[dataset], values)
            # Keep large deal lookup lists scoped to the selected branch and date range.
            if kind == 'deals' and dataset not in ('Sub', 'TC', 'DataSet1') and values.get('Division'):
                sql += ' AND ' if re.search(r'\bWHERE\b', sql, re.I) else ' WHERE '
                sql += '[Подразделение] = ? AND [ДатаЗакрытияСделки] BETWEEN ? AND ?'
                args.extend([values['Division'], values['StartDate'], values['EndDate']])
                if values.get('TypeCoeff'):
                    sql += ' AND [ТипКоэф] = ?'; args.append(values['TypeCoeff'])
            cache_key = 'motivation-report-choices-' + hashlib.sha256((signature+sql+repr(args)).encode()).hexdigest()
            dynamic_authors = kind in ('manager', 'manager-detail') and dataset == 'DA'
            options = None if refresh or dynamic_authors else cache.get(cache_key)
            if options is None:
                rows = fetch(cursor, sql, args, limit=10000)
                options = sorted({str(next(iter(row.values()))) for row in rows if next(iter(row.values())) is not None})
                if dynamic_authors:
                    options = [value for value in options if value.strip()]
                if not dynamic_authors:
                    cache.set(cache_key, options, 300)
            selected = values.get(parameter)
            selected = selected if isinstance(selected, list) else ([selected] if selected is not None else [])
            result.append({'name': name, 'label': caption, 'multiple': multiple,
                           'options': [{'value': item, 'selected': item in selected} for item in sorted(set(options) | set(selected))]})
        return result
    finally:
        if connection is not None: connection.close()


def number(value):
    if value is None: return Decimal(0)
    try: result = Decimal(str(value))
    except InvalidOperation: raise ValidationError('В отчёте есть некорректное числовое значение.')
    if not result.is_finite(): raise ValidationError('В отчёте есть некорректное числовое значение.')
    return result


def total(rows, key): return sum((number(row.get(key)) for row in rows), Decimal(0))


def ratio(actual, plan, zero_plan_is_actual=False):
    if not plan: return Decimal(1) if actual and zero_plan_is_actual else None
    return actual / plan


def manager_metrics(rows, group=False):
    q_plan = total(rows, 'СуммаПланUSDКварталValueQG' if group else 'СуммаПланUSDКварталValueQ')
    y_plan = total(rows, 'СуммаПланUSDКварталValueYG' if group else 'СуммаПланUSDКварталValue_Y')
    q_fact, y_fact = total(rows, 'СуммаОтгрузокUSDКварталValueQ'), total(rows, 'СуммаОтгрузокUSDКварталValue_Y')
    possible_q = total(rows, 'СуммаМотивацииUSDQ')
    possible_y = total(rows, 'СуммаМотивацииUSDQ_Y') + total(rows, 'СуммаМотивацииUSDY_Y')
    available_q = total(rows, 'СуммаМотивацииUSDУчетПроцентаQ')
    available_y = total(rows, 'СуммаМотивацииUSDУчетПроцентаQ_Y') + total(rows, 'СуммаМотивацииUSDУчетПроцентаY_Y')
    bonus = total(rows, 'Остаток')
    return [possible_q, max(q_plan-q_fact, 0) or None, ratio(q_fact, q_plan), available_q,
            possible_y, max(y_plan-y_fact, 0) or None, ratio(y_fact, y_plan), available_y, bonus, available_y+bonus]


def manager_report(rows):
    sections = []
    grouped = defaultdict(list)
    for row in rows: grouped[row.get('VariationCalculate') or 'Без группы'].append(row)
    for label, subset in sorted(grouped.items()):
        groups = defaultdict(list)
        for row in subset: groups[row.get('ГруппаПлановПродаж') or '—'].append(row)
        sections.append({'label': label, 'open': label.strip().casefold() != 'подразделение',
            'values': manager_metrics(subset, True), 'children': [{'label': name, 'values': manager_metrics(items, True)} for name, items in sorted(groups.items())]})
    return {'totals': manager_metrics(rows), 'sections': sections,
        'division_ratios': [ratio(total(rows, 'СуммаОтгрузокUSD_S_Q'), total(rows, 'СуммаПланUSD_S_Q')),
                            ratio(total(rows, 'СуммаОтгрузокUSD_S_Y'), total(rows, 'СуммаПланUSD_S_Y'))]}


DIV_NUMBERS = [*[f'O{i}' for i in range(5)], 'СуммаПланUSDКвартал', 'СуммаОтгрузокUSDКвартал',
 'СуммаМотивацииUSD', 'СуммаПланUSD', 'СуммаОтгрузокUSD', 'СуммаМотивацииUSDУчетПроцента', 'Остаток',
 'СуммаПланUSDI', 'СуммаОтгрузокUSDI']


def division_metrics(rows, annual=False, motivation_scope="plain"):
    divisor = Decimal(4 if annual else 2)
    plan, fact = total(rows, 'СуммаПланUSDКвартал')/divisor, total(rows, 'СуммаОтгрузокUSDКвартал')/divisor
    mp = total(rows, 'СуммаПланUSDI' if motivation_scope == 'planning' else 'СуммаПланUSD')/divisor
    mf = total(rows, 'СуммаОтгрузокUSDI' if motivation_scope == 'planning' else 'СуммаОтгрузокUSD')/divisor
    possible, paid = total(rows, 'СуммаМотивацииUSD'), total(rows, 'СуммаМотивацииUSDУчетПроцента')
    motivation = [mp, mf, ratio(mf, mp, True)] if motivation_scope else [None, None, None]
    return [*[total(rows, f'O{i}')/divisor for i in range(5)], plan, fact, ratio(fact, plan, True),
            possible, *motivation, paid, total(rows, 'Остаток'), paid+total(rows, 'Остаток')]


def subdivision_report(rows, scope='subdivisions'):
    periods = sorted({row.get('КварталМотивации') or 'Без периода' for row in rows}, key=lambda value: (value == 'Год', value))
    dimensions = ['Подразделение', 'ГруппаПлановПродаж', 'ГруппаТоваров', 'Марка'] if scope != 'matrix' else ['ГруппаПлановПродаж', 'ГруппаТоваров', 'Марка']
    def metrics(subset, level=None):
        planning_level = 0 if scope == 'matrix' else 1
        motivation_scope = 'plain' if level is None or (scope != 'matrix' and level == 0) else 'planning' if level == planning_level else None
        return [division_metrics([r for r in subset if (r.get('КварталМотивации') or 'Без периода') == period], motivation_scope=motivation_scope) for period in periods] + [division_metrics(subset, True, motivation_scope)]
    def nodes(subset, level=0):
        grouped = defaultdict(list)
        for row in subset: grouped[row.get(dimensions[level]) or '—'].append(row)
        return [{'label': label, 'level': level, 'values': metrics(items, level), 'children': nodes(items, level+1) if level+1<len(dimensions) else []}
                for label, items in sorted(grouped.items())]
    division_groups = defaultdict(list)
    for row in rows: division_groups[row.get('Подразделение') or '—'].append(row)
    def summary(subset):
        blocks = []
        for period in periods:
            items = [row for row in subset if (row.get('КварталМотивации') or 'Без периода')==period]
            blocks.append([total(items, 'СуммаМотивацииUSD'), total(items, 'СуммаМотивацииUSDУчетПроцента')])
        possible, paid, bonus = total(subset, 'СуммаМотивацииUSD'), total(subset, 'СуммаМотивацииUSDУчетПроцента'), total(subset, 'Остаток')
        return [value for block in blocks for value in block] + [possible, paid, bonus, paid+bonus]
    return {'periods': periods, 'nodes': nodes(rows) if rows else [], 'detail_totals': metrics(rows),
            'summary': [{'label': name, 'values': summary(items)} for name, items in sorted(division_groups.items())],
            'summary_totals': summary(rows)}


def manager_currency_sql(sql, currency):
    if currency not in ('USD', 'RUB'):
        raise ValidationError('Выберите валюту USD или RUB.')
    if currency == 'RUB':
        # Keep derived aliases stable for the shared renderer; replace source amounts only.
        sql = sql.replace('[СуммаМотивацииUSD]', '[СуммаМотивацииRUR]')
        sql = sql.replace('[СуммаМотивацииUSDУчетПроцента]', '[СуммаМотивацииRURУчетПроцента]')
        sql = sql.replace(',Остаток\n', ',[ОстатокRUR] AS [Остаток]\n')
    return sql


def load_report(kind, values, page=1, scope='subdivisions'):
    if kind == 'manager-detail' and not values.get('TypeCoeff_'):
        raise ValidationError('Выберите вид работника.')
    if kind == 'manager' and any(not values.get(key) for key in ('TypeCoeff_', 'Subdivision', 'DealAuthor', 'qm')):
        raise ValidationError('Выберите вид работника, подразделение, автора сделки и квартал.')
    if kind == 'deals' and (not values.get('TypeCoeff') or not values.get('Division')):
        raise ValidationError('Выберите тип коэффициента и подразделение.')
    connection = None
    try:
        connection, cursor = connect_database('vm-dwh', 'DataWH')
        connection.timeout = 60
        source_sql = (SQL_ROOT / f'{kind}.sql').read_text(encoding='utf-8')
        if kind == 'manager':
            source_sql = manager_currency_sql(source_sql, values.get('Currency', 'USD'))
        sql, args = bind_sql(source_sql, values)
        if kind == 'deals':
            source = '(' + sql + ') AS report_source'
            sums = 'SUM([СуммаОтгрузокUSD]) AS shipments, SUM([СуммаКвМотивацииUSD]) AS quarterly, SUM([СуммаМотивацииUSDГод]) AS annual, SUM([СуммаМотивацииUSD]) AS possible'
            summary = fetch(cursor, 'SELECT [АвторСделки] AS author,COUNT_BIG(*) AS count,'+sums+' FROM '+source+' GROUP BY [АвторСделки] ORDER BY [АвторСделки]', args)
            totals = {key: total(summary, key) for key in ['count','shipments','quarterly','annual','possible']}
            count = int(totals['count'])
            pages = max(1, (count+49)//50)
            page = min(max(1, page), pages)
            details = fetch(cursor, 'SELECT * FROM '+source+' ORDER BY [ДатаЗакрытияСделки] DESC,[Номер],[Модель],[АвторСделки] OFFSET ? ROWS FETCH NEXT 50 ROWS ONLY', [*args, (page-1)*50], 50)
            return {'summary': summary, 'totals': totals, 'details': details, 'page': page, 'pages': pages, 'offset': (page-1)*50}
        if kind == 'manager-detail':
            from .manager_detail import grouped_sql, manager_detail_report
            rows = fetch(cursor, grouped_sql(sql), args)
            performance_sql, performance_args = bind_sql(
                'SELECT [Подразделение],[Год],[КварталМотивации],'
                'SUM([СуммаПланUSDКвартал]) AS [СуммаПланUSDКвартал],'
                'SUM([СуммаОтгрузокUSDКвартал]) AS [СуммаОтгрузокUSDКвартал],'
                'SUM([СуммаПланUSD]) AS [СуммаПланUSD],SUM([СуммаОтгрузокUSD]) AS [СуммаОтгрузокUSD] '
                'FROM DataWH.motivation.ReportMotivationByDealAuthor '
                'WHERE [Подразделение] IN (@Subdivision) AND [ТипКоэф] = @TypeCoeff_ AND [Год] IN (@Years) '
                'GROUP BY [Подразделение],[Год],[КварталМотивации]', values)
            performance = fetch(cursor, performance_sql, performance_args)
            return manager_detail_report(rows, values.get('Currency', 'USD'), performance)
        if kind == 'subdivisions':
            dimensions = ['Подразделение', 'ГруппаПлановПродаж', 'ГруппаТоваров', 'Марка', 'КварталМотивации']
            columns = ','.join('['+key+']' for key in dimensions)
            sums = ','.join('SUM(['+key+']) AS ['+key+']' for key in DIV_NUMBERS)
            rows = fetch(cursor, 'SELECT '+columns+','+sums+' FROM ('+sql+') AS report_source GROUP BY '+columns, args)
            return subdivision_report(rows, scope)
        required = ('TypeCoeff_', 'Subdivision', 'DealAuthor', 'qm')
        if any(not values.get(key) for key in required): raise ValidationError('Выберите вид работника, подразделение, автора сделки и квартал.')
        rows = fetch(cursor, sql, args)
        report = manager_report(rows)
        # Read years from the source table: the legacy presentation view may omit Год.
        year_sql, year_args = bind_sql('SELECT DISTINCT [Год] FROM DataWH.motivation.ReportMotivationByDealAuthor '
            'WHERE [Подразделение] IN (@Subdivision) AND [ТипКоэф] = @TypeCoeff_ '
            'AND [АвторСделки] IN (@DealAuthor) AND [ГруппаПлановПродаж] IN (@gp_sales)', values)
        report['currency'] = values.get('Currency', 'USD')
        report['years'] = sorted({str(row['Год']) for row in fetch(cursor, year_sql, year_args) if row.get('Год') is not None})
        return report
    finally:
        if connection is not None: connection.close()
