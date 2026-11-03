"""Pilot deal amounts: Both quarterly and annual premiums use Q4 deals only."""
QUARTERLY_SQL = 'CASE WHEN MONTH([ДатаЗакрытияСделки]) >= 10 THEN COALESCE([СуммаКвМотивацииUSD], 0) ELSE 0 END'
ANNUAL_SQL = 'CASE WHEN MONTH([ДатаЗакрытияСделки]) >= 10 THEN COALESCE([СуммаМотивацииUSDГод], 0) ELSE 0 END'
POSSIBLE_SQL = f'({QUARTERLY_SQL}) + ({ANNUAL_SQL})'


def detail_projection():
    from .deal_presentation import COLUMNS
    fields = {key: f'[{key}]' for key, _, _ in COLUMNS}
    fields['СуммаКвМотивацииUSD'] = f'{QUARTERLY_SQL} AS [СуммаКвМотивацииUSD]'
    fields['СуммаМотивацииUSDГод'] = f'{ANNUAL_SQL} AS [СуммаМотивацииUSDГод]'
    fields['СуммаМотивацииUSD'] = f'{POSSIBLE_SQL} AS [СуммаМотивацииUSD]'
    return ','.join(fields.values())
