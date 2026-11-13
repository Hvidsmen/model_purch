"""Replace the legacy channel DELETE with the shared per-coefficient rules."""
import re
from django.core.exceptions import ValidationError
from .pilot_formula import mask_sql


def patch(source, *, policy_heat_all_years=False):
    pattern = re.compile(r"\bDELETE\s+(?:FROM\s+)?#pf_base\s+WHERE\s+\[?Канал\]?\s*=\s*N?'ДПП'(?P<guard>\s+AND\s+COALESCE\(LTRIM\(RTRIM\(\[ТипКоэф\]\)\),N''\)<>N'Директора')?", re.I)
    code = mask_sql(source)
    matches = [m for m in pattern.finditer(source) if code[m.start():m.start()+6].upper() == 'DELETE']
    if len(matches) != 1:
        raise ValidationError('Не распознано исключение канала ДПП. Расчёт не запущен.')
    match = matches[0]
    if re.match(r'\s*(?:AND|OR)\b', mask_sql(source)[match.end():], re.I):
        raise ValidationError('Не распознаны дополнительные условия исключения ДПП.')
    from .motivation_exclusions import sql_allowed
    replacement = 'DELETE FROM #pf_base WHERE NOT ('+sql_allowed()+')'
    return source[:match.start()] + replacement + source[match.end():]
