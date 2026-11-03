"""Annual share earned by shipment quarter, before annual payout eligibility.

Year report rows retain the shipment quarter and already contain the annual
share and annual subdivision correction. Quarterly projections can overlap
(Q2 includes Q1, Q4 includes the year), so they must never be used here.
"""
from decimal import Decimal


def grouped_sql(source, currency='USD'):
    field = 'СуммаМотивацииRUR' if currency == 'RUB' else 'СуммаМотивацииUSD'
    return f"""SELECT [Подразделение],[АвторСделки],[Год],[Квартал],
SUM([{field}]) AS AnnualAccrual
FROM ({source.strip().rstrip(';')}) AS annual_source
WHERE LOWER(LTRIM(RTRIM([КварталМотивации]))) = N'год'
AND [Квартал] IN (1,2,3,4)
GROUP BY [Подразделение],[АвторСделки],[Год],[Квартал]"""


def lookup(rows):
    result = {}
    for row in rows:
        key = (row.get('Подразделение') or '—', row.get('АвторСделки') or '—',
               str(row.get('Квартал')))
        value = row.get('AnnualAccrual')
        if value is not None:
            result[key] = result.get(key, Decimal(0)) + Decimal(str(value))
    return result


def total(rows):
    """Quarter contributions only: selecting the year must not count them twice."""
    values = [row.get('annual_accrual') for row in rows if row['code'] in ('1', '2', '3', '4')]
    return sum(values, Decimal(0)) if values and all(value is not None for value in values) else None


def enrich(display, ledger):
    if not any('annual_accrual' in row for row in ledger['rows']):
        return
    display['has_annual_accrual'] = True
    display['annual_accrual'] = total(ledger['rows'])
    codes = [column['code'] for column in display['period_columns']]
    for group in [*display['employees'], *display['registry_rows']]:
        group['annual_accrual'] = total(group['rows'])
        for code, cell in zip(codes, group['period_cells']):
            cell['annual_accrual'] = total([row for row in group['rows'] if row['code'] == code])
    for item in display['type_totals']:
        item['annual_accrual'] = total([row for row in ledger['rows'] if row['kind'] == item['kind']])
    for section in display['team_sections']:
        rows = [row for row in ledger['rows'] if row['kind'] == section['kind']]
        section['annual_accrual'] = total(rows)
        section['period_summary_cells'] = [
            {'amount': amount, 'annual_accrual': total([row for row in rows if row['code'] == code])}
            for code, amount in zip(codes, section['period_totals'])]
    for card in display['period_cards']:
        rows = [row for row in ledger['rows'] if row['code'] == card['code']]
        card['annual_accrual'] = total(rows)
        for item in card['type_totals']:
            item['annual_accrual'] = total([row for row in rows if row['kind'] == item['kind']])
