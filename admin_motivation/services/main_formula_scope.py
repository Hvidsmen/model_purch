"""Force ordinary formula projections; restore them for private shareholder SQL."""
import re
from django.core.exceptions import ValidationError
from .pilot_formula import mask_sql,closing

MARKER='/*__PORTAL_MAIN_GLOBAL_FORMULA__*/'
END='/*__END_PORTAL_MAIN_GLOBAL_FORMULA__*/'
PREFIX=MARKER+"COALESCE(N'Подразделение(Глобально)',"
PATTERN=re.compile(r'\bCOALESCE\s*\(\s*\[?sc\]?\s*\.\s*\[?VariationCalculate\]?\s*,',re.I)


def force(definition):
    if MARKER in definition:raise ValidationError('Формула основного расчёта уже закреплена.')
    code=mask_sql(definition)
    matches=list(PATTERN.finditer(code))
    if not matches:raise ValidationError('Не найдены формулы исходной процедуры основного расчёта.')
    for match in reversed(matches):
        end=closing(code,code.index('(',match.start()))+1
        original=definition[match.start():end]
        definition=definition[:match.start()]+PREFIX+original+')'+END+definition[end:]
    return definition


def shareholder_source(definition):
    if not definition or MARKER not in definition:return definition
    while MARKER in definition:
        start=definition.index(MARKER)
        if not definition.startswith(PREFIX,start):raise ValidationError('Не распознано ограничение формулы основного расчёта.')
        original_start=start+len(PREFIX)
        end=closing(mask_sql(definition),start+len(MARKER)+len('COALESCE'))
        if not definition.startswith(END,end+1):raise ValidationError('Не распознана граница формулы основного расчёта.')
        original=definition[original_start:end]
        if not PATTERN.match(mask_sql(original)):raise ValidationError('Не найдена исходная формула акционерного расчёта.')
        definition=definition[:start]+original+definition[end+1+len(END):]
    return definition
