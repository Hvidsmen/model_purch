"""Detailed manager RDL metrics, using grouped inputs and weighted coefficient averages."""
from collections import defaultdict
import re
from .reports import number, ratio, total

DIMENSIONS = ['Подразделение', 'АвторСделки', 'ГруппаПлановПродаж', 'ГруппаТоваров', 'Марка']
MONEY_FIELDS = ['СуммаМотивацииUSD', 'СуммаМотивацииUSDУчетПроцента', 'Остаток',
                'СуммаМотивацииRUR', 'СуммаМотивацииRURУчетПроцента', 'ОстатокRUR']
SUM_FIELDS = [*[f'O{i}' for i in range(5)], 'СуммаПланUSDКвартал', 'СуммаОтгрузокUSDКвартал',
              'СуммаПланUSD', 'СуммаОтгрузокUSD', *MONEY_FIELDS]
HEADERS = ['K0', 'K1', 'K2', 'K3', 'K4', 'Отгрузки O0', 'Отгрузки O1', 'Отгрузки O2',
           'Отгрузки O3', 'Отгрузки O4', 'План периода, USD', 'Факт периода, USD', '% вып. периода',
           'Возможная мотивация', 'План мотивации, USD', 'Факт мотивации, USD', '% вып. мотивации',
           'С учётом % выполнения', 'Доплата за кварталы', 'Итого мотивация']


def grouped_sql(source):
    fields = [*DIMENSIONS, 'Год', 'КварталМотивации']
    columns = ','.join(f'[{key}]' for key in fields)
    sums = ','.join(f'SUM([{key}]) AS [{key}]' for key in SUM_FIELDS)
    averages = ','.join(f'SUM([{source}]) AS [K{i}Sum],COUNT([{source}]) AS [K{i}Count]'
                        for i, source in enumerate(['K0', 'K1', 'K2', 'k3', 'k4']))
    return f'SELECT {columns},{sums},{averages} FROM ({source}) AS manager_source GROUP BY {columns}'


def metrics(rows, currency='USD', overall=False, coefficients=False):
    inputs = rows
    if overall:
        annual = [row for row in rows if str(row.get('КварталМотивации') or '').strip().casefold() == 'год']
        def scope(row):
            return tuple(row.get(key) for key in [*DIMENSIONS, 'Год'])
        inputs = annual if annual and {scope(row) for row in annual} == {scope(row) for row in rows} else None
    prefix = 'RUR' if currency == 'RUB' else 'USD'
    plan, fact = (total(inputs, 'СуммаПланUSDКвартал'), total(inputs, 'СуммаОтгрузокUSDКвартал')) if inputs is not None else (None, None)
    motive_plan, motive_fact = (total(inputs, 'СуммаПланUSD'), total(inputs, 'СуммаОтгрузокUSD')) if inputs is not None else (None, None)
    paid = total(rows, f'СуммаМотивации{prefix}УчетПроцента')
    bonus = total(rows, 'ОстатокRUR' if currency == 'RUB' else 'Остаток')
    averages = []
    for index in range(5):
        count = total(inputs or [], f'K{index}Count')
        averages.append(total(inputs or [], f'K{index}Sum') / count if coefficients and count else None)
    return [*averages, *[total(inputs, f'O{i}') if inputs is not None else None for i in range(5)], plan, fact, ratio(fact, plan, True),
            total(rows, f'СуммаМотивации{prefix}'), motive_plan, motive_fact, ratio(motive_fact, motive_plan, True),
            paid, bonus, paid + bonus]


def period_motivation_summary(rows):
    buckets = defaultdict(list)
    captions = {}
    for row in rows:
        year = str(row['Год']) if row.get('Год') is not None else 'Без года'
        name = str(row.get('КварталМотивации') or 'Без периода').strip()
        match = re.match(r'^([1-4])\s*кв', name, re.I)
        segment = int(match.group(1)) if match else 'year' if name.casefold() == 'год' else name
        buckets[year, segment].append(row)
        captions.setdefault((year, segment), set()).add(name)
    years = sorted({year for year, segment in buckets}) or ['']
    columns, keys = [], []
    for year in years:
        segments = [1, 2, 3, 4, 'year'] + sorted(
            {segment for scope_year, segment in buckets if scope_year == year and segment not in (1, 2, 3, 4, 'year')})
        for segment in segments:
            key = (year, segment)
            label = f'{segment} кв.' if isinstance(segment, int) else 'Год' if segment == 'year' else segment
            columns.append({'label': f'{year} · {label}' if len(years) > 1 else label,
                            'title': ', '.join(sorted(captions.get(key, ())))})
            keys.append(key)
    currencies = []
    for currency, paid_key, bonus_key in [('USD', 'СуммаМотивацииUSDУчетПроцента', 'Остаток'),
                                           ('RUB', 'СуммаМотивацииRURУчетПроцента', 'ОстатокRUR')]:
        values = [total(buckets[key], paid_key) if key in buckets else None for key in keys]
        bonus = total(rows, bonus_key)
        currencies.append({'currency': currency, 'values': values, 'remainder': bonus,
                           'total': total(rows, paid_key) + bonus})
    return {'columns': columns, 'rows': currencies}


def manager_detail_report(rows, currency='USD', performance_rows=None):
    years = sorted({str(row['Год']) for row in rows if row.get('Год') is not None})
    year_scopes = {str(row.get('Год')) for row in rows}
    def period(row):
        name = row.get('КварталМотивации') or 'Без периода'
        return f'{row.get("Год") or "Без года"} · {name}' if len(year_scopes) > 1 else name
    periods = sorted({period(row) for row in rows}, key=lambda name: ('Год' in name, name))
    def blocks(subset, coefficients=False):
        grouped = defaultdict(list)
        for row in subset:
            grouped[period(row)].append(row)
        return [metrics(grouped[name], currency, coefficients=coefficients) for name in periods] + [
            metrics(subset, currency, overall=True, coefficients=coefficients)]
    def nodes(subset, level=0):
        grouped = defaultdict(list)
        for row in subset:
            grouped[row.get(DIMENSIONS[level]) or '—'].append(row)
        return [{'label': label, 'level': level, 'values': blocks(items, level == len(DIMENSIONS) - 1),
                 'children': nodes(items, level + 1) if level + 1 < len(DIMENSIONS) else []}
                for label, items in sorted(grouped.items())]
    cards = []
    for label, paid_key, bonus_key in [('USD', 'СуммаМотивацииUSDУчетПроцента', 'Остаток'),
                                     ('RUB', 'СуммаМотивацииRURУчетПроцента', 'ОстатокRUR')]:
        paid, bonus = total(rows, paid_key), total(rows, bonus_key)
        cards.append({'currency': label, 'paid': paid, 'bonus': bonus, 'total': paid + bonus})
    performance = []
    for row in performance_rows or []:
        plan, fact = number(row.get('СуммаПланUSDКвартал')), number(row.get('СуммаОтгрузокUSDКвартал'))
        motive_plan, motive_fact = number(row.get('СуммаПланUSD')), number(row.get('СуммаОтгрузокUSD'))
        performance.append({'subdivision': row.get('Подразделение') or '—', 'year': row.get('Год'),
            'period': row.get('КварталМотивации') or 'Без периода',
            'values': [plan, fact, ratio(fact, plan, True), motive_plan, motive_fact, ratio(motive_fact, motive_plan, True)]})
    performance.sort(key=lambda row: (row['subdivision'], str(row['year']), row['period']))
    # Align branch execution with the visible periods/years of the main report.
    visible_years = {str(row.get('Год')) for row in rows}
    execution_rows = [row for row in performance_rows or [] if str(row.get('Год')) in visible_years]
    execution = []
    if execution_rows:
        for values in blocks(execution_rows):
            block = [None] * len(HEADERS)
            block[12], block[16] = values[12], values[16]
            execution.append(block)
    return {'periods': periods, 'nodes': nodes(rows), 'detail_totals': blocks(rows, True),
            'headers': [{'label': label, 'detail': index < 13} for index, label in enumerate(HEADERS)],
            'cards': cards, 'years': years, 'currency': currency, 'division_performance': performance, 'division_execution': execution, 'period_summary': period_motivation_summary(rows)}
