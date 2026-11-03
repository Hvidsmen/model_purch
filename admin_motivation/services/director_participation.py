"""Keep director rows when applying legacy channel/subdivision exclusions."""
import re
from django.core.exceptions import ValidationError
from .pilot_formula import mask_sql

EXCLUDED = ('ДПП', 'REtail', 'E-Com', 'СНГ', 'Д-Маврикий', 'Буфер', 'Брендинг')
MARKER = '-- Portal channel/subdivision exclusions apply to non-director coefficients only.'


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
    if {name.replace("''", "'").casefold() for name in names} != {name.casefold() for name in EXCLUDED}:
        raise ValidationError('Изменился список исключённых подразделений в SQL. Требуется проверка правила для директоров.')
    channel_pattern = re.compile(
        r"\bDELETE\s+(?:FROM\s+)?#pf_base\s+WHERE\s+\[?Канал\]?\s*=\s*N?'ДПП'", re.I)
    channels = [item for item in channel_pattern.finditer(definition)
                if mask[item.start():item.start() + 6].upper() == 'DELETE']
    if len(channels) != 1:
        raise ValidationError('Не распознан фильтр канала ДПП для директоров. Расчёт не запущен.')
    if any(re.match(r'\s*(?:AND|OR)\b', mask[item.end():], re.I) for item in [match, channels[0]]):
        raise ValidationError('Изменилась структура фильтров каналов или подразделений. Требуется проверка правила для директоров.')
    guard = " AND COALESCE(LTRIM(RTRIM([ТипКоэф])),N'')<>N'Директора'"
    for item in sorted([match, channels[0]], key=lambda item: item.start(), reverse=True):
        definition = definition[:item.end()] + guard + definition[item.end():]
    return MARKER + '\n' + definition
