"""Pilot deal amounts: Q4 earns quarterly and annual, Q1–Q3 annual only."""
QUARTERLY_SQL = 'CASE WHEN MONTH([ДатаЗакрытияСделки]) >= 10 THEN COALESCE([СуммаКвМотивацииUSD], 0) ELSE 0 END'
ANNUAL_SQL = 'COALESCE([СуммаМотивацииUSDГод], 0)'
POSSIBLE_SQL = f'({QUARTERLY_SQL}) + ({ANNUAL_SQL})'


def detail_projection():
    from .deal_presentation import COLUMNS
    fields = {key: f'[{key}]' for key, _, _ in COLUMNS}
    fields['СуммаКвМотивацииUSD'] = f'{QUARTERLY_SQL} AS [СуммаКвМотивацииUSD]'
    fields['СуммаМотивацииUSD'] = f'{POSSIBLE_SQL} AS [СуммаМотивацииUSD]'
    return ','.join(fields.values())
