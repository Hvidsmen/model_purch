"""Quarterly annual contributions, independent of annual payout eligibility."""
from decimal import Decimal

SNAPSHOT_VERSION = 'quarter-snapshot-v1'
TABLE = 'DataWH.motivation.AnnualAccrual'
DIMENSIONS = ('Подразделение', 'АвторСделки', 'Год', 'Квартал',
              'ГруппаПлановПродаж', 'ГруппаТоваров', 'Марка', 'ТипКоэф')


def snapshot_table(variant):
    suffixes = {'main': '', 'all_2026': 'All2026', 'separate_2026': 'Separate2026'}
    if variant not in suffixes:
        raise ValueError('Unknown calculation variant')
    return TABLE + suffixes[variant]


def patch_calculation(definition):
    """Capture the excluded/typed base, never overlapping payout projections."""
    import re
    from django.core.exceptions import ValidationError
    from .pilot_formula import mask_sql
    mask = mask_sql(definition)
    if re.search(r'\bmotivation\.AnnualAccrual\b', mask, re.I):
        raise ValidationError('Снимок годовой части уже присутствует в исходной процедуре.')
    first = re.search(r"N?'1\s+Квартал'\s+(?:AS\s+)?\[?КварталМотивации\]?", definition, re.I)
    if not first:
        raise ValidationError('Не найден первый квартал для сохранения годовой части.')
    selects = list(re.finditer(r'\bSELECT\b', mask[:first.start()], re.I))
    deletes = list(re.finditer(r'\bDELETE\s+(?:FROM\s+)?#pf_base\b', mask[:first.start()], re.I))
    if not selects or not deletes or selects[-1].start() <= deletes[-1].start():
        raise ValidationError('Не найдена точка годовой части после исключений мотивации.')
    point = selects[-1].start()
    columns = ','.join('[' + name + ']' for name in DIMENSIONS)
    snapshot = f"""
-- Annual contributions by original quarter: informational, never added to payouts.
IF OBJECT_ID(N'{TABLE}',N'U') IS NOT NULL DROP TABLE {TABLE};
SELECT {columns},
 SUM([СуммаМотивацииUSDГод]) AS AnnualUSD,
 SUM([СуммаМотивацииRURГод]) AS AnnualRUB
INTO {TABLE}
FROM #pf_base
GROUP BY {columns};
"""
    definition = definition[:point] + snapshot + definition[point:]
    # Match the same year correction and effective date as the annual payment.
    # Quarter corrections must not be applied to the annual contribution.
    drop = 'DROP TABLE #portal_manager_correction;'
    if '#portal_manager_correction' in mask.casefold():
        if definition.count(drop) != 1:
            raise ValidationError('Не найдена годовая поправка для снимка годовой части.')
        correction = f"""
UPDATE a SET AnnualUSD=a.AnnualUSD*COALESCE(k.Coeff_,1),
 AnnualRUB=a.AnnualRUB*COALESCE(k.Coeff_,1)
FROM {TABLE} a
OUTER APPLY (SELECT TOP (1) c.Coeff_ FROM #portal_manager_correction c
 WHERE c.Subdivision=COALESCE((SELECT m.TargetName FROM motivation.PortalSubdivisionMerge m
 WHERE m.SourceName=a.[Подразделение]),a.[Подразделение])
 AND c.PeriodId=5 AND c.Date_<=DATEFROMPARTS(a.[Год],12,31)
 ORDER BY c.Date_ DESC) k;
"""
        definition = definition.replace(drop, correction + drop)
    return definition


def snapshot_sql(variant='main', currency='USD', *, director_roster=False):
    from .subdivision_scope import sql_predicate
    field = 'AnnualRUB' if currency == 'RUB' else 'AnnualUSD'
    condition = ('[Подразделение] IN (@Subdivision) AND [АвторСделки] IN (@DealAuthor) '
                 'AND [ТипКоэф]=@TypeCoeff_ AND [Год] IN (@Years) '
                 'AND [ГруппаПлановПродаж] IN (@gp_sales) '
                 'AND [ГруппаТоваров] IN (@GPGoods) AND [Марка] IN (@Brend) '
                 'AND [Квартал] IN (1,2,3,4) AND ' + sql_predicate('[Подразделение]'))
    if director_roster:
        from .active_directors import REPORT_CONDITION
        condition += ' AND ' + REPORT_CONDITION
    return ('SELECT [Подразделение],[АвторСделки],[Год],[Квартал],SUM([' + field + ']) AS AnnualAccrual '
            'FROM ' + snapshot_table(variant) + ' WHERE ' + condition +
            ' GROUP BY [Подразделение],[АвторСделки],[Год],[Квартал]')


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
