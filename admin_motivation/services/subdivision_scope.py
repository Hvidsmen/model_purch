"""Shared participation rules for motivation plans, coefficients and SQL sources."""
EXCLUDED = ('Вентиляция',)
POLICY = 'motivation-subdivisions-v1-without-ventilation'


def included(name, aliases=None):
    aliases = aliases or {}
    raw = str(name or '').strip()
    target = str(aliases.get(raw, raw) or '').strip()
    excluded = {value.casefold() for value in EXCLUDED}
    return raw.casefold() not in excluded and target.casefold() not in excluded


def sql_predicate(expression):
    literals = ','.join("UPPER(N'" + value.replace("'", "''") + "')" for value in EXCLUDED)
    return f"UPPER(LTRIM(RTRIM(COALESCE({expression},N'')))) NOT IN ({literals})"


def excluded_plan_names(scenario):
    from .subdivision_aliases import mapping
    aliases = mapping()
    return {name for name in scenario.lines.exclude(subdivision='').order_by().values_list('subdivision', flat=True).distinct()
            if not included(name, aliases)}
