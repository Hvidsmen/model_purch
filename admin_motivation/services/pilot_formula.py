"""Adapt supported live calculation expressions; never change the source procedure."""
import re
from django.core.exceptions import ValidationError

GLOBAL_SUBDIVISION = 'Подразделение(Глобально)'


def mask_sql(sql):
    return re.sub(r"--[^\n]*|/\*[\s\S]*?\*/|N?'(?:''|[^'])*'", lambda match: ' ' * len(match[0]), sql, flags=re.I)


def closing(mask, start):
    level = 0
    for index in range(start, len(mask)):
        if mask[index] == '(':
            level += 1
        elif mask[index] == ')':
            level -= 1
            if level == 0:
                return index
    raise ValidationError('Не распознаны скобки SQL-формулы. Исходная процедура не изменена.')


def department_window(sql, with_year=False):
    # Zero-plan fact rows add no plan; do not add one unit per sale/employee.
    sql = re.sub(r'SUM\s*\(\s*IIF\s*\(\s*(\[?\w+\]?)\s*=\s*0\s*,\s*1\s*,\s*\1\s*\)\s*\)',
                 r'SUM(\1)', sql, flags=re.I)
    def replace(match):
        columns = [value.strip() for value in match[1].split(',')]
        columns = [value for value in columns if value.strip('[] ').casefold() != 'авторсделки']
        if with_year and not any(value.strip('[] ').casefold() == 'год' for value in columns):
            columns.append('Год')
        return 'PARTITION BY ' + ','.join(columns)
    return re.sub(r'PARTITION\s+BY\s+([^()]+?)(?=\))', replace, sql, flags=re.I)


def patch_calculation(definition):
    """Extend recognized CASE formulae and their enclosing author payout gates.

    Personal rows and personal potential amounts remain unchanged. Only the
    performance ratio and eligibility guards use subdivision totals for the new mode.
    Unknown source shapes fail before the portal procedure can be created/altered.
    """
    if GLOBAL_SUBDIVISION in definition:
        raise ValidationError('В исходной процедуре уже есть формула Подразделение(Глобально). Требуется проверка совместимости SQL.')
    pattern = re.compile(r"WHEN\s+(?:\[?VariationCalculate\]?)\s*=\s*N?'Подразделение'\s+THEN\s+", re.I)
    original_mask = mask_sql(definition)
    matches = []
    for match in pattern.finditer(definition):
        if original_mask[match.start():match.start() + 4].upper() != 'WHEN':
            continue
        boundary = re.search(r'\b(?:WHEN|ELSE)\b', original_mask[match.end():], re.I)
        if boundary is None:
            raise ValidationError('Не найдены границы SQL-формулы подразделения.')
        end = match.end() + boundary.start()
        matches.append((match.start(), end, definition[match.end():end]))
    if not matches:
        raise ValidationError('В SQL не найдены поддерживаемые формулы подразделения. Пилотный расчёт не запущен.')
    window_cases = 0
    for start, end, expression in reversed(matches):
        if re.fullmatch(r'\s*CONCAT\([^()]+\)\s*', expression, re.I):
            continue  # Keep personal row/classification keys; only performance gates change.
        if not re.search(r'OVER\s*\(\s*PARTITION\s+BY', expression, re.I) or not re.search(r'\bАвторСделки\b', expression, re.I):
            raise ValidationError('Не распознана формула подразделения по автору. Пилотный расчёт не запущен.')
        window_cases += 1
        global_expression = department_window(expression, with_year=True)
        if '/' in global_expression:
            numerator, denominator = global_expression.split('/', 1)
            global_expression = f'{numerator} / NULLIF(({denominator.strip()}),0)\n '
        replacement = f"WHEN VariationCalculate = N'{GLOBAL_SUBDIVISION}' THEN\n {global_expression}\n " + definition[start:end]
        definition = definition[:start] + replacement + definition[end:]
    if not window_cases:
        raise ValidationError('Не найдены оконные формулы выполнения плана для пилота.')
    mask = mask_sql(definition)
    cases = []
    for case in re.finditer(r'\bCASE\b', mask, re.I):
        # Supported formula CASE blocks contain no nested CASE expressions.
        end = re.search(r'\bEND\b', mask[case.end():], re.I)
        if end:
            end_index = case.end() + end.end()
            if GLOBAL_SUBDIVISION in definition[case.start():end_index]:
                cases.append((case.start(), end_index))
    regions = []
    for case_start, case_end in cases:
        enclosing = []
        for function in re.finditer(r'\bIIF\s*\(', mask[:case_start], re.I):
            end = closing(mask, function.end() - 1)
            if end >= case_end:
                enclosing.append((function.start(), end + 1))
        if not enclosing:
            # Projected plan/fact CASE expressions are not payout conditions.
            continue
        regions.append(enclosing[-1])
    if not regions:
        raise ValidationError('Не найдены границы расчёта выплаты. Пилотный расчёт не запущен.')
    edits = []
    for over in re.finditer(r'\bOVER\s*\(', mask, re.I):
        end = closing(mask, over.end() - 1) + 1
        window = definition[over.start():end]
        if not re.search(r'\bАвторСделки\b', window, re.I):
            continue
        if any(start <= over.start() < stop for start, stop in cases):
            continue
        if not any(start <= over.start() < stop for start, stop in regions):
            continue
        aggregate_end = over.start()
        while aggregate_end and mask[aggregate_end - 1].isspace():
            aggregate_end -= 1
        if not aggregate_end or mask[aggregate_end - 1] != ')':
            raise ValidationError('Не распознан контроль выполнения плана в SQL.')
        level, aggregate_start = 0, None
        for index in range(aggregate_end - 1, -1, -1):
            if mask[index] == ')':
                level += 1
            elif mask[index] == '(':
                level -= 1
                if level == 0:
                    name = re.search(r'\bSUM\s*$', mask[:index], re.I)
                    aggregate_start = name.start() if name else None
                    break
        if aggregate_start is None:
            raise ValidationError('Не распознан агрегат контроля выплаты. Пилотный расчёт не запущен.')
        original = definition[aggregate_start:end]
        global_total = department_window(original)
        edits.append((aggregate_start, end, f"IIF(VariationCalculate=N'{GLOBAL_SUBDIVISION}',{global_total},{original})"))
    for start, end, text in reversed(edits):
        definition = definition[:start] + text + definition[end:]
    return global_payout_guards(definition)


def global_payout_guards(definition):
    """Global mode has one department gate, independent of personal gates."""
    mask = mask_sql(definition)
    edits = []
    for function in re.finditer(r'\bIIF\s*\(', mask, re.I):
        opening = function.end() - 1
        end = closing(mask, opening)
        depth, commas = 0, []
        for index in range(opening + 1, end):
            if mask[index] == '(':
                depth += 1
            elif mask[index] == ')':
                depth -= 1
            elif mask[index] == ',' and depth == 0:
                commas.append(index)
        if len(commas) != 2:
            continue
        start, stop = opening + 1, commas[0]
        condition = definition[start:stop]
        yes = mask[commas[0] + 1:commas[1]].strip()
        no = mask[commas[1] + 1:end].strip()
        if not (yes == '0' or (yes == '1' and no == '0')):
            continue
        branch = re.search(r"WHEN\s+VariationCalculate\s*=\s*N?'" + re.escape(GLOBAL_SUBDIVISION) +
                           r"'\s+THEN\s+(.*?)\s+WHEN\b", condition, re.I | re.S)
        if not branch or '/' not in branch[1]:
            continue
        threshold = re.search(r'\bEND\s*<\s*(@PayoutThreshold|0?\.80*)\b', condition, re.I)
        if threshold is None:
            raise ValidationError('Не распознан порог глобальной выплаты.')
        ratio = branch[1].strip()
        numerator, denominator = ratio.split('/', 1)
        ratio = f'COALESCE(({numerator}) / NULLIF(({denominator}),0),0)'
        mode = f"VariationCalculate=N'{GLOBAL_SUBDIVISION}'"
        replacement = (f'(({mode} AND {ratio} < {threshold[1]}) OR '
                       f'(CASE WHEN {mode} THEN 1 ELSE 0 END=0 AND ({condition})))')
        edits.append((start, stop, replacement))
    for start, stop, replacement in reversed(edits):
        definition = definition[:start] + replacement + definition[stop:]
    return definition


def default_variation():
    from ..models import VariationCalculate
    return VariationCalculate.objects.exclude(variation_name=GLOBAL_SUBDIVISION).order_by('pk').first() or VariationCalculate.objects.get_or_create(variation_name='Группа планов продаж')[0]
