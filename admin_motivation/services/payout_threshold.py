"""Parameterize recognized payout guards in the portal copy of the procedure."""
import re
from django.core.exceptions import ValidationError
from .pilot_formula import mask_sql, closing


def parameterize(definition):
    mask = mask_sql(definition)
    header = re.search(r'\b(?:CREATE\s+(?:OR\s+ALTER\s+)?|ALTER\s+)PROC(?:EDURE)?\b.*?\bAS\b', mask, re.I | re.S)
    if not header:
        raise ValidationError('Не распознан заголовок процедуры для порога выплаты.')
    guards = []
    for function in re.finditer(r'\bIIF\s*\(', mask, re.I):
        start = function.end() - 1
        end = closing(mask, start)
        depth, commas = 0, []
        for index in range(start + 1, end):
            if mask[index] == '(':
                depth += 1
            elif mask[index] == ')':
                depth -= 1
            elif mask[index] == ',' and depth == 0:
                commas.append(index)
        if len(commas) != 2:
            continue
        condition = mask[start + 1:commas[0]]
        yes = mask[commas[0] + 1:commas[1]].strip()
        no = mask[commas[1] + 1:end].strip()
        if '/' in condition and re.search(r'\bOVER\b', condition, re.I) and (yes == '0' or (yes == '1' and no == '0')):
            guards.append((start + 1, commas[0]))
    replacements = []
    for comparison in re.finditer(r'(?<![<>=!])<\s*(0\.80*)(?![\w.])', mask):
        if not any(start <= comparison.start() < end for start, end in guards):
            raise ValidationError('Обнаружено нераспознанное условие порога 0.8. Исходная процедура не изменена.')
        replacements.append((comparison.start(1), comparison.end(1), '@PayoutThreshold'))
    existing = re.search(r'@PayoutThreshold\b', mask[header.start():header.end()], re.I)
    if not replacements and not (existing and any(re.search(r'<\s*@PayoutThreshold\b', mask[start:end], re.I) for start, end in guards)):
        raise ValidationError('Не найдены поддерживаемые условия порога выплаты. Расчёт не запущен.')
    if not existing:
        if re.search(r'@PayoutThreshold\b', mask, re.I):
            raise ValidationError('Имя параметра порога уже используется в теле процедуры.')
        as_start = header.end() - 2
        # A leading comma on its own line is safe after line comments in the header.
        separator = ',' if '@' in mask[header.start():as_start] else ''
        replacements.append((as_start, as_start, f'\n{separator} @PayoutThreshold decimal(9,8) = 0.8\n'))
    for start, end, value in sorted(replacements, reverse=True):
        definition = definition[:start] + value + definition[end:]
    return definition
