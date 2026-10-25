"""Q1 has no motivation target: both execution inputs equal period shipments."""
import re


def normalize(rows):
    result = []
    for row in rows:
        if re.fullmatch(r'1\s*квартал', str(row.get('КварталМотивации') or '').strip(), re.I):
            row = dict(row)
            fact = row.get('СуммаОтгрузокUSDКвартал')
            row['СуммаПланUSD'] = fact
            row['СуммаОтгрузокUSD'] = fact
        result.append(row)
    return result


def patch(definition):
    # Match only the two supported Q1 output projections followed by its period
    # literal. Never alter payment gates, later quarters or the source procedure.
    from .pilot_formula import mask_sql
    pattern = re.compile(
        r'(?P<first>SUM\s*\(\s*\[?СуммаОтгрузокUSD\]?\s*\)\s*OVER\s*\(\s*PARTITION\s+BY\s+\[?KeyPlan\]?\s*,\s*\[?ТипКоэф\]?\s*\))'
        r'\s+(?:AS\s+)?\[?СуммаОтгрузокUSD\]?\s*,\s*'
        r'(?P<second>SUM\s*\(\s*\[?СуммаОтгрузокUSD\]?\s*\)\s*OVER\s*\(\s*PARTITION\s+BY\s+\[?KeyPlan\]?\s*,\s*\[?ТипКоэф\]?\s*\))'
        r"\s+(?:AS\s+)?\[?СуммаПланUSD\]?\s*,\s*N?'1\s+Квартал'\s+(?:AS\s+)?\[?КварталМотивации\]?", re.I)
    mask = mask_sql(definition)
    edits = []
    for match in pattern.finditer(definition):
        if mask[match.start():match.start() + 3].upper() != 'SUM':
            continue
        for name in ('first', 'second'):
            edits.append((*match.span(name), 'СуммаОтгрузокUSD'))
    for start, end, value in sorted(edits, reverse=True):
        definition = definition[:start] + value + definition[end:]
    return definition
