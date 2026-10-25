"""Detailed manager RDL metrics, using grouped inputs and shipment-weighted coefficient averages."""
from collections import defaultdict
import re
from .reports import number, ratio, total

def unallocated_author(value):
    return str(value or '').strip().casefold() == 'политики'


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
    fields = [*DIMENSIONS, 'Год', 'КварталМотивации', 'VariationCalculate']
    columns = ','.join(f'[{key}]' for key in fields)
    sums = ','.join(f'SUM([{key}]) AS [{key}]' for key in SUM_FIELDS)
    averages = ','.join(f'SUM([{source}] * [O{i}]) AS [K{i}WeightedSum],'
                        f'SUM(CASE WHEN [{source}] IS NOT NULL THEN [O{i}] ELSE 0 END) AS [K{i}Weight]'
                        for i, source in enumerate(['K0', 'K1', 'K2', 'k3', 'k4']))
    return f'SELECT {columns},{sums},{averages} FROM ({source}) AS manager_source GROUP BY {columns}'


def metrics(rows, currency='USD', overall=False, coefficients=True):
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
        weight = total(inputs or [], f'K{index}Weight')
        averages.append(total(inputs or [], f'K{index}WeightedSum') / weight if coefficients and weight else None)
    return [*averages, *[total(inputs, f'O{i}') if inputs is not None else None for i in range(5)], plan, fact, ratio(fact, plan, True),
            total(rows, f'СуммаМотивации{prefix}'), motive_plan, motive_fact, ratio(motive_fact, motive_plan, True),
            paid, bonus, paid + bonus]


def period_motivation_summary(rows, currency='USD', pilot=False):
    def period_key(row):
        year = str(row['Год']) if row.get('Год') is not None else 'Без года'
        name = str(row.get('КварталМотивации') or 'Без периода').strip()
        match = re.match(r'^([1-4])\s*кв', name, re.I)
        segment = int(match.group(1)) if match else 'year' if name.casefold() == 'год' else name
        return year, segment, name
    buckets = defaultdict(list)
    captions = {}
    for row in rows:
        year, segment, name = period_key(row)
        buckets[year, segment].append(row)
        captions.setdefault((year, segment), set()).add(name)
    years = sorted({year for year, segment in buckets}) or ['']
    columns, keys = [], []
    for year in years:
        segments = [1, 2, 3, 4, 'year'] + sorted(
            {segment for scope_year, segment in buckets if scope_year == year and segment not in (1, 2, 3, 4, 'year')})
        if pilot:
            segments = [4, 'year']
        for segment in segments:
            key = (year, segment)
            label = f'{segment} кв.' if isinstance(segment, int) else 'Год' if segment == 'year' else segment
            columns.append({'label': f'{year} · {label}' if len(years) > 1 else label,
                            'title': ', '.join(sorted(captions.get(key, ())))})
            keys.append(key)
    paid_key = 'СуммаМотивацииRURУчетПроцента' if currency == 'RUB' else 'СуммаМотивацииUSDУчетПроцента'
    bonus_key = 'ОстатокRUR' if currency == 'RUB' else 'Остаток'
    def summary(subset):
        grouped = defaultdict(list)
        for row in subset:
            year, segment, name = period_key(row)
            grouped[year, segment].append(row)
        bonus = total(subset, bonus_key)
        return {'currency': currency,
                'values': [total(grouped[key], paid_key) if key in grouped else None for key in keys],
                'remainder': bonus, 'total': total(subset, paid_key) + bonus}
    authors = defaultdict(list)
    for row in rows:
        authors[row.get('Подразделение') or '—', row.get('АвторСделки') or '—'].append(row)
    details = [{'subdivision': sub, 'author': author, 'unallocated': unallocated_author(author), **summary(subset)}
               for (sub, author), subset in sorted(authors.items())]
    subdivisions = defaultdict(list)
    for row in rows:
        subdivisions[row.get('Подразделение') or '—'].append(row)
    authors_by_subdivision = defaultdict(list)
    for row in details:
        authors_by_subdivision[row['subdivision']].append(row)
    groups = [{'subdivision': sub, **summary(subset),
               'authors': authors_by_subdivision[sub]}
              for sub, subset in sorted(subdivisions.items())]
    return {'columns': columns, 'rows': details, 'groups': groups,
            'totals': summary(rows), 'currency': currency}


def manager_detail_report(rows, currency='USD', performance_rows=None, pilot=False):
    if pilot:
        from .report_periods import pilot_rows
        rows = pilot_rows(rows)
        if performance_rows is not None:
            performance_rows = pilot_rows(performance_rows)
    years = sorted({str(row['Год']) for row in rows if row.get('Год') is not None})
    year_scopes = {str(row.get('Год')) for row in rows}
    def period(row):
        name = row.get('КварталМотивации') or 'Без периода'
        return f'{row.get("Год") or "Без года"} · {name}' if len(year_scopes) > 1 else name
    periods = sorted({period(row) for row in rows}, key=lambda name: ('Год' in name, name))
    def execution_metrics(subset, values, overall=False, context=None, target_period=None):
        from .pilot_formula import GLOBAL_SUBDIVISION
        modes = {str(row.get('VariationCalculate') or '').strip().casefold() for row in (subset or context or [])}
        if GLOBAL_SUBDIVISION.casefold() not in modes:
            return values
        # A global-subdivision formula has personal shipments, but no personal
        # period target. Mixing it with other formulae has no additive execution ratio.
        values[10], values[12] = None, None
        if modes != {GLOBAL_SUBDIVISION.casefold()}:
            values[14:17] = [None, None, None]
            return values
        expected = {(row.get('Подразделение'), str(row.get('Год')), str(row.get('КварталМотивации') or '').strip()) for row in subset}
        if not subset and context and target_period:
            raw_period = target_period
            scopes = {(row.get('Подразделение'), str(row.get('Год'))) for row in context}
            if len(year_scopes) > 1:
                target_year, raw_period = target_period.split(' · ', 1)
                scopes = {(sub, year) for sub, year in scopes if year == target_year}
            expected = {(sub, year, str(raw_period).strip()) for sub, year in scopes}
        if overall:
            scopes = {(sub, year) for sub, year, _ in expected}
            department = [row for row in performance_rows or [] if
                (row.get('Подразделение'), str(row.get('Год'))) in scopes and
                str(row.get('КварталМотивации') or '').strip().casefold() == 'год']
            complete = {(row.get('Подразделение'), str(row.get('Год'))) for row in department} == scopes
        else:
            department = [row for row in performance_rows or [] if
                (row.get('Подразделение'), str(row.get('Год')), str(row.get('КварталМотивации') or '').strip()) in expected]
            complete = {(row.get('Подразделение'), str(row.get('Год')), str(row.get('КварталМотивации') or '').strip()) for row in department} == expected
        if not department or not complete:
            values[14:17] = [None, None, None]
        else:
            plan, fact = total(department, 'СуммаПланUSD'), total(department, 'СуммаОтгрузокUSD')
            values[14:17] = [plan, fact, ratio(fact, plan, True)]
        return values

    def blocks(subset, coefficients=True):
        grouped = defaultdict(list)
        for row in subset:
            grouped[period(row)].append(row)
        return [execution_metrics(grouped[name], metrics(grouped[name], currency, coefficients=coefficients), context=subset, target_period=name) for name in periods] + [
            execution_metrics(subset, metrics(subset, currency, overall=True, coefficients=coefficients), overall=True)]
    def nodes(subset, level=0):
        grouped = defaultdict(list)
        for row in subset:
            grouped[row.get(DIMENSIONS[level]) or '—'].append(row)
        return [{'label': label, 'level': level, 'unallocated': level == 1 and unallocated_author(label), 'values': blocks(items),
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
    visible_branches = {row.get('Подразделение') for row in rows}
    execution_rows = [row for row in performance_rows or [] if str(row.get('Год')) in visible_years and row.get('Подразделение') in visible_branches]
    execution = []
    if execution_rows:
        by_period = defaultdict(list)
        for row in execution_rows:
            by_period[period(row)].append(row)
        department_blocks = [metrics(by_period[name], currency, coefficients=False) for name in periods] + [
            metrics(execution_rows, currency, overall=True, coefficients=False)]
        for values in department_blocks:
            block = [None] * len(HEADERS)
            for index in (10, 11, 12, 14, 15, 16):
                block[index] = values[index]
            execution.append(block)
    from .pilot_formula import GLOBAL_SUBDIVISION
    global_mode = any(str(row.get('VariationCalculate') or '').strip().casefold() == GLOBAL_SUBDIVISION.casefold() for row in rows)
    return {'has_unallocated': any(unallocated_author(row.get('АвторСделки')) for row in rows), 'global_subdivision_mode': global_mode, 'periods': periods, 'nodes': nodes(rows), 'detail_totals': blocks(rows, True),
            'headers': [{'label': label, 'detail': index < 13, 'title': f'Средневзвешенный K{index}: Σ(K{index} × O{index}) / Σ(O{index})' if index < 5 else label} for index, label in enumerate(HEADERS)],
            'cards': [card for card in cards if card['currency'] == currency], 'years': years, 'currency': currency, 'division_performance': performance, 'division_execution': execution, 'period_summary': period_motivation_summary(rows, currency, pilot=pilot)}
