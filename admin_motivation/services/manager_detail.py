"""Detailed manager RDL metrics, using grouped inputs and weighted coefficient averages."""
from collections import defaultdict
from decimal import Decimal
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
    divisor = Decimal(2 if overall else 1)  # RDL overall SUM/2, not subdivision report SUM/4.
    prefix = 'RUR' if currency == 'RUB' else 'USD'
    plan, fact = total(rows, 'СуммаПланUSDКвартал') / divisor, total(rows, 'СуммаОтгрузокUSDКвартал') / divisor
    motive_plan, motive_fact = total(rows, 'СуммаПланUSD') / divisor, total(rows, 'СуммаОтгрузокUSD') / divisor
    paid = total(rows, f'СуммаМотивации{prefix}УчетПроцента')
    bonus = total(rows, 'ОстатокRUR' if currency == 'RUB' else 'Остаток')
    averages = []
    for index in range(5):
        count = total(rows, f'K{index}Count')
        averages.append(total(rows, f'K{index}Sum') / count if coefficients and count else None)
    return [*averages, *[total(rows, f'O{i}') / divisor for i in range(5)], plan, fact, ratio(fact, plan, True),
            total(rows, f'СуммаМотивации{prefix}'), motive_plan, motive_fact, ratio(motive_fact, motive_plan, True),
            paid, bonus, paid + bonus]


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
    return {'periods': periods, 'nodes': nodes(rows), 'detail_totals': blocks(rows, True),
            'headers': [{'label': label, 'detail': index < 13} for index, label in enumerate(HEADERS)],
            'cards': cards, 'years': years, 'currency': currency, 'division_performance': performance}
