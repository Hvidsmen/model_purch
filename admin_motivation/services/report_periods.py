"""Trusted pilot restrictions for motivation reports; deal facts stay unrestricted."""
PILOT_QUARTER = '4 Квартал(1-4 кв)'
PILOT_SQL = "(LOWER(LTRIM(RTRIM([КварталМотивации]))) = N'год' OR LOWER(LTRIM(RTRIM([КварталМотивации]))) LIKE N'4 кв%')"


def pilot_enabled(kind, scope):
    return kind in ('manager', 'manager-detail') and bool(scope and scope.get('pilot_periods'))


def allowed_period(value):
    name = str(value or '').strip().casefold()
    return name == 'год' or name.startswith('4 кв')


def pilot_rows(rows):
    return [row for row in rows if allowed_period(row.get('КварталМотивации'))]
