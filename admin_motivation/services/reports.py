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
    'subdivisions': {'title': 'Мотивация по подразделениям', 'description': 'Итоги по филиалам и раскрываемая детализация товарной матрицы.'},
    'manager': {'title': 'Мотивация. Дашборд', 'description': 'Квартал и год, выполнение плана, доступная мотивация и доплата за кварталы.'},
    'manager-detail': {'title': 'Мотивация. Детально', 'description': 'Подробный отчёт: подразделения, сотрудники, кварталы, коэффициенты и товарная матрица. USD и RUB.'},
    'deals': {'title': 'Факт закрытых сделок', 'description': 'Отгрузки и возможная мотивация по сделкам, менеджерам и товарной матрице.'},
}
PREMIUM_PERIODS = [('1', '1 квартал'), ('2', '2 квартал'), ('3', '3 квартал'), ('4', '4 квартал'), ('5', 'Год')]

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
                  ('brand', 'Brend', 'Марка / бренд', 'Brend', True),
                  ('period', 'PremiumPeriods', 'Период премии', '_premium_periods', True)],
}


def parameters(kind, query, trusted_identity=False):
    values = {}
    for name, parameter, _, _, multi in FILTERS[kind]:
        items = query.getlist(name) if hasattr(query, 'getlist') else query.get(name, [])
        if isinstance(items, str): items = [items]
        if (len(items) > 100 and not (trusted_identity and name == 'subdivision')) or any(len(value) > 255 for value in items):
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
    if kind == 'subdivisions':
        periods = values.get('PremiumPeriods', [])
        if any(value not in dict(PREMIUM_PERIODS) for value in periods):
            raise ValidationError('Выберите период премии: 1–4 квартал или год.')
        if not periods:
            values.pop('PremiumPeriods', None)
    from .subdivision_scope import included
    for key in ('Subdivision', 'Division'):
        selected = values.get(key) or []
        selected = selected if isinstance(selected, list) else [selected]
        if any(not included(value) for value in selected):
            raise ValidationError('Подразделение «Вентиляция» исключено из мотивации.')
    return values


def bind_sql(sql, parameters):
    """Expand lists without inserting values into SQL; empty optional lists mean all."""
    from .subdivision_aliases import report_parameters
    sql, parameters = report_parameters(sql, parameters)
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
    from .subdivision_aliases import report_rows
    return report_rows([dict(zip(columns, row)) for row in rows])


def choices(kind, values, refresh=False, access_scope=None):
    from .subdivision_scope import included
    if access_scope:
        from base.report_access import scoped_values
        from .personal_reports import choices as personal_choices
        return personal_choices(kind, scoped_values(kind, values, access_scope), access_scope)
    queries = json.loads((SQL_ROOT / 'lookups.json').read_text(encoding='utf-8'))[kind]
    connection = None
    signature = hashlib.sha256(str(getattr(settings, 'DWH_SQL_CONN_STR', '')).encode()).hexdigest()[:16]
    try:
        connection, cursor = connect_database('vm-dwh', 'DataWH')
        connection.timeout = 30
        result = []
        for name, parameter, caption, dataset, multiple in FILTERS[kind]:
            if dataset == '_premium_periods':
                result.append({'name': name, 'label': caption, 'multiple': multiple, 'options': [{'value': value, 'label': label, 'selected': value in values.get(parameter, [])} for value, label in PREMIUM_PERIODS]})
                continue
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
                from .subdivision_aliases import expand, mapping
                branches = expand(values['Division'], mapping())
                sql += '[Подразделение] IN (' + ','.join('?' for _ in branches) + ') AND [ДатаЗакрытияСделки] BETWEEN ? AND ?'
                args.extend([*branches, values['StartDate'], values['EndDate']])
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
                           'options': [{'value': item, 'selected': item in selected} for item in sorted(set(options) | set(selected)) if parameter not in ('Subdivision', 'Division') or included(item)]})
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


def dashboard_blocks(rows, group=False, period_rows=(), periods="12345"):
    metrics = manager_metrics(rows, group)
    result = []
    for scope, offset in [('quarter', 0), ('year', 4)]:
        prefix = 'Q' if scope == 'quarter' else 'Y'
        plan_key = 'СуммаПланUSDКварталValue' + ('QG' if group else 'Q') if scope == 'quarter' else 'СуммаПланUSDКварталValue' + ('YG' if group else '_Y')
        fact_key = 'СуммаОтгрузокUSDКварталValue' + ('Q' if scope == 'quarter' else '_Y')
        def value(items, key):
            return total(items, key) if any(row.get(key) is not None for row in items) else None
        cells = [
            {'value': metrics[offset]},
            {'value': metrics[offset + 1], 'reveal': scope + '-period'},
            {'value': value(period_rows, prefix + 'PeriodPlan'), 'hidden': scope + '-period'},
            {'value': value(period_rows, prefix + 'PeriodFact'), 'hidden': scope + '-period'},
            {'value': metrics[offset + 2], 'percent': True, 'reveal': scope + '-motivation'},
            {'value': value(rows, plan_key), 'hidden': scope + '-motivation'},
            {'value': value(rows, fact_key), 'hidden': scope + '-motivation'},
            {'value': metrics[offset + 3]},
        ]
        if scope == 'year':
            cells[0]['reveal'] = 'year-possible'
            cells[7]['reveal'] = 'year-available'
            for kind, quarter_field, year_field in [
                ('possible', 'СуммаМотивацииUSDQ_Y', 'СуммаМотивацииUSDY_Y'),
                ('available', 'СуммаМотивацииUSDУчетПроцентаQ_Y', 'СуммаМотивацииUSDУчетПроцентаY_Y'),
            ]:
                for period in map(int, periods):
                    subset = [row for row in rows if (
                        str(row.get('КварталМотивации') or '').strip().casefold() == 'год' if period == 5
                        else re.match(r'^' + str(period) + r'\s*Квартал', str(row.get('КварталМотивации') or ''), flags=re.I))]
                    cells.append({'value': value(subset, year_field if period == 5 else quarter_field), 'hidden': 'year-' + kind})
            cells += [{'value': metrics[8]}, {'value': metrics[9]}]
        result.extend(cells)
    return result


def department_execution(rows, values, blocks):
    """Use the same full-subdivision data as the dashboard's execution header."""
    for value_offset, block_offset, suffix in ((0, 0, 'Q'), (4, 8, 'Y')):
        plan_key, fact_key = 'СуммаПланUSD_S_' + suffix, 'СуммаОтгрузокUSD_S_' + suffix
        plan = total(rows, plan_key) if any(row.get(plan_key) is not None for row in rows) else None
        fact = total(rows, fact_key) if any(row.get(fact_key) is not None for row in rows) else None
        percentage = ratio(fact, plan) if fact is not None and plan is not None else None
        remaining = max(plan - fact, 0) or None if plan is not None and fact is not None else None
        values[value_offset + 1], values[value_offset + 2] = remaining, percentage
        for index, value in ((1, remaining), (2, plan), (3, fact), (4, percentage), (5, plan), (6, fact)):
            blocks[block_offset + index]['value'] = value


def manager_report(rows, period_rows=(), pilot=False):
    from .report_periods import pilot_rows
    if pilot:
        rows = pilot_rows(rows)
    periods = '45' if pilot else '12345'
    def blocks(items, group=False, period_rows=()):
        return dashboard_blocks(items, group, period_rows, periods)
    sections = []
    grouped = defaultdict(list)
    for row in rows: grouped[row.get('VariationCalculate') or 'Без группы'].append(row)
    for label, subset in sorted(grouped.items()):
        groups = defaultdict(list)
        for row in subset: groups[row.get('ГруппаПлановПродаж') or '—'].append(row)
        sections.append({'label': label, 'open': label.strip().casefold() not in {'подразделение', 'подразделение(глобально)'},
            'values': manager_metrics(subset, True), 'blocks': blocks(subset, True, [row for row in period_rows if row.get('VariationCalculate') == label]), 'children': [{'label': name, 'values': manager_metrics(items, True), 'blocks': blocks(items, True, [row for row in period_rows if row.get('VariationCalculate') == label and (row.get('ГруппаПлановПродаж') or '—') == name])} for name, items in sorted(groups.items())]})
    from .pilot_formula import GLOBAL_SUBDIVISION
    for section in sections:
        if section['label'] == GLOBAL_SUBDIVISION:
            department_execution(rows, section['values'], section['blocks'])
            for child in section['children']:
                department_execution(rows, child['values'], child['blocks'])
    totals, total_blocks = manager_metrics(rows), blocks(rows, period_rows=period_rows)
    payout_modes = {row.get('VariationCalculate') for row in rows if any(row.get(key) is not None for key in ('СуммаМотивацииUSDQ', 'СуммаМотивацииUSDQ_Y', 'СуммаМотивацииUSDY_Y'))}
    if payout_modes == {GLOBAL_SUBDIVISION}:
        department_execution(rows, totals, total_blocks)
    return {'year_periods': periods, 'totals': totals, 'total_blocks': total_blocks, 'sections': sections,
        'division_ratios': [ratio(total(rows, 'СуммаОтгрузокUSD_S_Q'), total(rows, 'СуммаПланUSD_S_Q')),
                            ratio(total(rows, 'СуммаОтгрузокUSD_S_Y'), total(rows, 'СуммаПланUSD_S_Y'))]}


DIV_NUMBERS = [*[f'O{i}' for i in range(5)], 'СуммаПланUSDКвартал', 'СуммаОтгрузокUSDКвартал',
 'СуммаМотивацииUSD', 'СуммаПланUSD', 'СуммаОтгрузокUSD', 'СуммаМотивацииUSDУчетПроцента', 'Остаток',
 'СуммаПланUSDI', 'СуммаОтгрузокUSDI']


DIV_INPUT_DIMENSIONS = ('_source_subdivision', 'Подразделение', 'АвторСделки', 'Год', 'ГруппаПлановПродаж', 'ГруппаТоваров', 'Марка')
DIV_INPUT_FIELDS = (*[f'O{i}' for i in range(5)], 'СуммаПланUSDКвартал', 'СуммаОтгрузокUSDКвартал',
                    'СуммаПланUSD', 'СуммаОтгрузокUSD', 'СуммаПланUSDI', 'СуммаОтгрузокUSDI')


def division_metrics(rows, annual=False, motivation_scope="plain"):
    def identity(row):
        return tuple(row.get(key) for key in DIV_INPUT_DIMENSIONS)
    inputs = rows
    if annual:
        annual_rows = [row for row in rows if str(row.get('КварталМотивации') or '').strip().casefold() == 'год']
        inputs = annual_rows if annual_rows and {identity(row) for row in annual_rows} == {identity(row) for row in rows} else None
    # Sales inputs repeat for each coefficient type. Keep every employee/year/
    # matrix scope distinct, but count its sales once, independent of type count.
    unique = {}
    for row in inputs or []:
        key = (*identity(row), row.get('КварталМотивации'))
        current = unique.setdefault(key, {})
        for field in DIV_INPUT_FIELDS:
            value = row.get(field)
            if value is not None:
                value = number(value)
                current[field] = max(current[field], value) if field in current else value
    def input_total(field):
        return total(unique.values(), field) if inputs is not None else None
    plan, fact = input_total('СуммаПланUSDКвартал'), input_total('СуммаОтгрузокUSDКвартал')
    mp = input_total('СуммаПланUSDI' if motivation_scope == 'planning' else 'СуммаПланUSD')
    mf = input_total('СуммаОтгрузокUSDI' if motivation_scope == 'planning' else 'СуммаОтгрузокUSD')
    possible, paid, bonus = total(rows, 'СуммаМотивацииUSD'), total(rows, 'СуммаМотивацииUSDУчетПроцента'), total(rows, 'Остаток')
    motivation = [mp, mf, ratio(mf, mp, True) if mp is not None else None] if motivation_scope else [None, None, None]
    return [*[input_total(f'O{i}') for i in range(5)], plan, fact,
            ratio(fact, plan, True) if plan is not None else None,
            possible, *motivation, paid, bonus, paid + bonus]


def subdivision_report(rows, scope='subdivisions'):
    years = {row.get('Год') for row in rows}
    def period_label(row):
        period = row.get('КварталМотивации') or 'Без периода'
        return f'{row.get("Год") or "Без года"} · {period}' if len(years) > 1 else period
    periods = sorted({period_label(row) for row in rows}, key=lambda value: (value.endswith('Год'), value))
    dimensions = ['Подразделение', 'ГруппаПлановПродаж', 'ГруппаТоваров', 'Марка'] if scope != 'matrix' else ['ГруппаПлановПродаж', 'ГруппаТоваров', 'Марка']
    def metrics(subset, level=None):
        planning_level = 0 if scope == 'matrix' else 1
        motivation_scope = 'plain' if level is None or (scope != 'matrix' and level == 0) else 'planning' if level == planning_level else None
        return [division_metrics([r for r in subset if period_label(r) == period], motivation_scope=motivation_scope) for period in periods] + [division_metrics(subset, True, motivation_scope)]
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
            items = [row for row in subset if period_label(row)==period]
            blocks.append([total(items, 'СуммаМотивацииUSD'), total(items, 'СуммаМотивацииUSDУчетПроцента')])
        possible, paid, bonus = total(subset, 'СуммаМотивацииUSD'), total(subset, 'СуммаМотивацииUSDУчетПроцента'), total(subset, 'Остаток')
        return [value for block in blocks for value in block] + [possible, paid, bonus, paid+bonus]
    return {'years': sorted({str(value) for value in years if value is not None}), 'periods': periods, 'nodes': nodes(rows) if rows else [], 'detail_totals': metrics(rows),
            'summary': [{'label': name, 'values': summary(items),
                         'children': [{'label': type_name, 'values': summary([row for row in items if (row.get('ТипКоэф') or 'Без типа коэффициента') == type_name])}
                                      for type_name in sorted({row.get('ТипКоэф') or 'Без типа коэффициента' for row in items})]}
                        for name, items in sorted(division_groups.items())],
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


def load_report(kind, values, page=1, scope='subdivisions', access_scope=None, premium_period=None, summary_only=False):
    if access_scope:
        from base.report_access import scoped_values, worker_scope
        values = scoped_values(kind, values, access_scope)
        access_scope = worker_scope(access_scope, kind, values)
    from .report_periods import pilot_enabled, PILOT_SQL
    pilot = pilot_enabled(kind, access_scope)
    if premium_period is not None and premium_period not in ('all', '1', '2', '3', '4', '5'):
        raise ValidationError('Выберите период премии.')
    period_condition = "LOWER(LTRIM(RTRIM([КварталМотивации]))) = N'год'" if premium_period == '5' else f"LOWER(LTRIM(RTRIM([КварталМотивации]))) LIKE N'{premium_period} кв%'" if premium_period in ('1', '2', '3', '4') else ''
    if kind == 'manager-detail' and not values.get('TypeCoeff_'):
        raise ValidationError('Выберите вид работника.')
    if kind == 'manager' and any(not values.get(key) for key in (('TypeCoeff_', 'Subdivision', 'qm') if access_scope and access_scope.get('all_authors') else ('TypeCoeff_', 'Subdivision', 'DealAuthor', 'qm'))):
        raise ValidationError('Выберите вид работника, подразделение, автора сделки и квартал.')
    if kind == 'deals' and (not values.get('TypeCoeff') or not values.get('Division')):
        raise ValidationError('Выберите тип коэффициента и подразделение.')
    connection = None
    try:
        connection, cursor = connect_database('vm-dwh', 'DataWH')
        connection.timeout = 60
        source_sql = (SQL_ROOT / f'{kind}.sql').read_text(encoding='utf-8')
        from .subdivision_scope import sql_predicate
        source_sql = 'SELECT * FROM (' + source_sql.strip().rstrip(';') + ') AS included_source WHERE ' + sql_predicate('[Подразделение]')
        if kind == 'manager-detail' and period_condition:
            source_sql += ' AND ' + period_condition
        if kind == 'manager':
            source_sql = manager_currency_sql(source_sql, values.get('Currency', 'USD'))
        if access_scope and kind == 'manager':
            source_sql = 'SELECT * FROM (' + source_sql + ') AS personal_source WHERE [АвторСделки] IN (@DealAuthor)'
        if pilot:
            source_sql = 'SELECT * FROM (' + source_sql + ') AS pilot_source WHERE ' + PILOT_SQL
        sql, args = bind_sql(source_sql, values)
        if kind == 'subdivisions' and values.get('PremiumPeriods'):
            periods = values['PremiumPeriods']
            sql = ('SELECT * FROM (' + sql + ') AS premium_source WHERE '
                   "CASE WHEN LOWER(LTRIM(RTRIM([КварталМотивации]))) = N'год' THEN N'5' "
                   "ELSE LEFT(LTRIM([КварталМотивации]),1) END IN (" + ','.join('?' for _ in periods) + ')')
            args.extend(periods)
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
            rows = fetch(cursor, grouped_sql(sql, summary_only=summary_only), args)
            if summary_only:
                from .manager_detail import period_motivation_summary
                if premium_period and premium_period != 'all':
                    from .reports_next import period_code
                    rows = [row for row in rows if period_code(row.get('КварталМотивации')) == premium_period]
                return {'period_summary': period_motivation_summary(rows, values.get('Currency', 'USD'), pilot=pilot), 'currency': values.get('Currency', 'USD')}
            performance_fields = ('СуммаПланUSDКвартал', 'СуммаОтгрузокUSDКвартал', 'СуммаПланUSD', 'СуммаОтгрузокUSD')
            performance_source = ('DataWH.motivation.ReportMotivationByDealAuthor WHERE ' + sql_predicate('[Подразделение]') +
                ' AND [Подразделение] IN (@Subdivision) AND [ТипКоэф]=@TypeCoeff_ AND [Год] IN (@Years)' +
                (' AND ' + PILOT_SQL if pilot else '') + (' AND ' + period_condition if period_condition else ''))
            if str(values.get('TypeCoeff_') or '').strip().casefold() == 'директора':
                # Multiple directors share the same subdivision sales/plan inputs.
                dimensions = '[Подразделение],[Год],[КварталМотивации],[ГруппаПлановПродаж],[ГруппаТоваров],[Марка]'
                performance_source = '(SELECT ' + dimensions + ',' + ','.join(f'MAX([{field}]) AS [{field}]' for field in performance_fields) + ' FROM ' + performance_source + ' GROUP BY ' + dimensions + ') AS director_inputs'
            performance_sql, performance_args = bind_sql(
                'SELECT [Подразделение],[Год],[КварталМотивации],' +
                ','.join(f'SUM([{field}]) AS [{field}]' for field in performance_fields) +
                ' FROM ' + performance_source + ' GROUP BY [Подразделение],[Год],[КварталМотивации]', values)
            performance = fetch(cursor, performance_sql, performance_args)
            if premium_period and premium_period != 'all':
                from .reports_next import period_code
                rows = [row for row in rows if period_code(row.get('КварталМотивации')) == premium_period]
                performance = [row for row in performance if period_code(row.get('КварталМотивации')) == premium_period]
            return manager_detail_report(rows, values.get('Currency', 'USD'), performance, pilot=pilot)
        if kind == 'subdivisions':
            dimensions = ['Подразделение', 'АвторСделки', 'Год', 'ТипКоэф', 'ГруппаПлановПродаж', 'ГруппаТоваров', 'Марка', 'КварталМотивации']
            columns = ','.join('['+key+']' for key in dimensions)
            sums = ','.join('SUM(['+key+']) AS ['+key+']' for key in DIV_NUMBERS)
            rows = fetch(cursor, 'SELECT '+columns+','+sums+' FROM ('+sql+') AS report_source GROUP BY '+columns, args)
            return subdivision_report(rows, scope)
        required = ('TypeCoeff_', 'Subdivision', 'qm') if access_scope and access_scope.get('all_authors') else ('TypeCoeff_', 'Subdivision', 'DealAuthor', 'qm')
        if any(not values.get(key) for key in required): raise ValidationError('Выберите вид работника, подразделение, автора сделки и квартал.')
        rows = fetch(cursor, sql, args)
        period_sql, period_args = bind_sql(
            'SELECT [VariationCalculate],[ГруппаПлановПродаж],'
            'SUM(CASE WHEN [КварталМотивации]=@qm THEN [СуммаПланUSDКвартал] END) AS QPeriodPlan,'
            'SUM(CASE WHEN [КварталМотивации]=@qm THEN [СуммаОтгрузокUSDКвартал] END) AS QPeriodFact,'
            "SUM(CASE WHEN [КварталМотивации]='Год' THEN [СуммаПланUSDКвартал] END) AS YPeriodPlan,"
            "SUM(CASE WHEN [КварталМотивации]='Год' THEN [СуммаОтгрузокUSDКвартал] END) AS YPeriodFact "
            'FROM DataWH.motivation.ReportMotivationByDealAuthor '
            'WHERE ' + sql_predicate('[Подразделение]') + ' AND [Подразделение] IN (@Subdivision) AND [ТипКоэф]=@TypeCoeff_ '
            'AND [АвторСделки] IN (@DealAuthor) AND [ГруппаПлановПродаж] IN (@gp_sales) '
            + ('AND ' + PILOT_SQL + ' ' if pilot else '') + 'GROUP BY [VariationCalculate],[ГруппаПлановПродаж]', values)
        period_rows = fetch(cursor, period_sql, period_args)
        report = manager_report(rows, period_rows, pilot=pilot)
        # Read years from the source table: the legacy presentation view may omit Год.
        year_sql, year_args = bind_sql('SELECT DISTINCT [Год] FROM DataWH.motivation.ReportMotivationByDealAuthor '
            'WHERE ' + sql_predicate('[Подразделение]') + ' AND [Подразделение] IN (@Subdivision) AND [ТипКоэф] = @TypeCoeff_ '
            'AND [АвторСделки] IN (@DealAuthor) AND [ГруппаПлановПродаж] IN (@gp_sales)' + (' AND ' + PILOT_SQL if pilot else ''), values)
        report['currency'] = values.get('Currency', 'USD')
        report['years'] = sorted({str(row['Год']) for row in fetch(cursor, year_sql, year_args) if row.get('Год') is not None})
        return report
    finally:
        if connection is not None: connection.close()
