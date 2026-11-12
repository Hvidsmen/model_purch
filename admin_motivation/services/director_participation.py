"""Keep director rows when applying legacy channel/subdivision exclusions."""
import re
from django.core.exceptions import ValidationError
from .pilot_formula import mask_sql

from .subdivision_scope import EXCLUDED,sql_predicate
LEGACY_EXCLUDED = ('ДПП', 'REtail', 'E-Com', 'СНГ', 'Д-Маврикий', 'Буфер', 'Брендинг')
MARKER = '-- Portal shared subdivision-name exclusion directory applies to every coefficient type.'


def patch(definition):
    if MARKER in definition:
        return definition
    mask = mask_sql(definition)
    if not re.search(r'#pf_base\b', mask, re.I):
        return definition
    pattern = re.compile(
        r'\bDELETE\s+(?:FROM\s+)?#pf_base\s+WHERE\s+\[?Подразделение\]?\s+IN\s*'
        r"\((?P<names>\s*N?'(?:''|[^'])*'(?:\s*,\s*N?'(?:''|[^'])*')*\s*)\)", re.I)
    matches = [match for match in pattern.finditer(definition)
               if mask[match.start():match.start() + 6].upper() == 'DELETE']
    if len(matches) != 1:
        raise ValidationError('Не распознан фильтр исключённых подразделений для директоров. Расчёт не запущен.')
    match = matches[0]
    names = re.findall(r"N?'((?:''|[^'])*)'", match['names'], re.I)
    if {name.replace("''", "'").casefold() for name in names} != {name.casefold() for name in LEGACY_EXCLUDED}:
        raise ValidationError('Изменился список исключённых подразделений в SQL. Требуется проверка правила для директоров.')
    channel_pattern = re.compile(
        r"\bDELETE\s+(?:FROM\s+)?#pf_base\s+WHERE\s+\[?Канал\]?\s*=\s*N?'ДПП'", re.I)
    channels = [item for item in channel_pattern.finditer(definition)
                if mask[item.start():item.start() + 6].upper() == 'DELETE']
    if len(channels) != 1:
        raise ValidationError('Не распознан фильтр канала ДПП для директоров. Расчёт не запущен.')
    if any(re.match(r'\s*(?:AND|OR)\b', mask[item.end():], re.I) for item in [match, channels[0]]):
        raise ValidationError('Изменилась структура фильтров каналов или подразделений. Требуется проверка правила для директоров.')
    # Name exclusions apply to all kinds. Channel DPP is handled separately
    # by sales_participation and is excluded only for policies.
    definition = definition[:match.start()] + 'DELETE FROM #pf_base WHERE NOT (' + sql_predicate('[Подразделение]') + ')' + definition[match.end():]
    return MARKER + '\n' + definition
