"""Isolated 2025 inputs, fact-based policy allocation and sample-shaped exports."""
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from io import BytesIO
import hashlib
import json
from openpyxl import load_workbook, Workbook
from openpyxl.styles import Font, PatternFill
from django.core.exceptions import ValidationError

VARIANT = 'test_2025'
RULE_VERSION = 'fact25-plan25-v6-author-share-hierarchy'
PERIODS = ['1 Квартал', '2 Квартал(1-2 кв)', '3 Квартал', '4 Квартал(1-4 кв)', 'Год']
KINDS = ('Политики', 'Продажи')


def decimal(value):
    try:
        result = Decimal(str(value).replace('\u00a0', '').replace(' ', '').replace(',', '.'))
        if not result.is_finite():
            raise ValueError()
        return result
    except (ValueError, InvalidOperation, TypeError):
        raise ValidationError('Некорректное числовое значение.')


def read_coefficients(upload):
    """The supplied workbook has an empty E column; header positions are detected."""
    raw = upload.read(5 * 1024 * 1024 + 1)
    if len(raw) > 5 * 1024 * 1024:
        raise ValidationError('Файл коэффициентов должен быть не больше 5 МБ.')
    try:
        book = load_workbook(BytesIO(raw), read_only=True, data_only=False)
        sheet = book.active
        if sheet.max_row > 5000 or sheet.max_column > 100:
            raise ValidationError('Слишком большой диапазон коэффициентов.')
        rows = list(sheet.iter_rows(values_only=True))
    except ValidationError:
        raise
    except Exception:
        raise ValidationError('Не удалось прочитать файл XLSX.')
    finally:
        if 'book' in locals():
            book.close()
    bands = {}
    for index, value in enumerate(rows[0] if rows else []):
        if str(value or '').strip() in KINDS:
            bands[str(value).strip()] = index
    if set(bands) != set(KINDS) or len(rows) < 3:
        raise ValidationError('Нужны два блока: «Политики» и «Продажи», в каждом K0–K4.')
    for offset in bands.values():
        if [str(v or '').strip().upper().replace('К', 'K') for v in rows[1][offset:offset + 5]] != [f'K{i}' for i in range(5)]:
            raise ValidationError('В блоке коэффициентов должны быть K0–K4 по порядку.')
    result, seen = [], set()
    for index, row in enumerate(rows[2:], 3):
        if not any(v is not None for v in row):
            continue
        if len(row) < 4 or not all(str(v or '').strip() for v in row[1:4]):
            raise ValidationError(f'Строка {index}: заполните группу планов, группу ERP и бренд (или *).')
        key = tuple(str(v).strip() for v in row[1:4])
        if key in seen:
            raise ValidationError(f'Строка {index}: повторное товарное сочетание.')
        seen.add(key)
        record = dict(zip(('planning', 'goods', 'brand'), key))
        for kind, offset in bands.items():
            values = []
            for column in range(offset, offset + 5):
                value = row[column] if column < len(row) else None
                if value is None or isinstance(value, bool) or str(value).startswith('='):
                    raise ValidationError(f'Строка {index}: все K0–K4 должны быть числами, без формул.')
                number = decimal(value).quantize(Decimal('.000001'))
                if not 0 <= number <= 1:
                    raise ValidationError(f'Строка {index}: ставка должна быть от 0 до 100%.')
                values.append(str(number))
            record[kind] = values
        result.append(record)
    if not result:
        raise ValidationError('Файл не содержит коэффициентов.')
    return result, hashlib.sha256(raw).hexdigest()


def summary(rows):
    """Amounts are already calculated by SQL; do not recalculate bonuses in UI."""
    grouped = {kind: defaultdict(lambda: [Decimal(0)] * 5) for kind in KINDS}
    bonuses = {kind: defaultdict(lambda: Decimal(0)) for kind in KINDS}
    for row in rows:
        kind, branch, author, period, amount, *extra = row
        bonus = decimal(extra[0] or 0) if extra else Decimal(0)
        if kind not in KINDS:
            continue
        code = '5' if str(period).strip().casefold() == 'год' else str(period).strip()[:1]
        if code not in '12345' or len(code) != 1:
            raise ValidationError('Неизвестный период в результате 2025 года.')
        key = (str(branch), str(author or '').strip() or 'Не заполнено') if kind == 'Политики' else (str(branch), '')
        grouped[kind][key][int(code) - 1] += decimal(amount or 0)
        bonuses[kind][key] += bonus
    sections = []
    for kind in KINDS:
        branches = defaultdict(list)
        totals = [Decimal(0)] * 5
        for (branch, author), values in sorted(grouped[kind].items()):
            branches[branch].append({'branch': branch, 'author': author, 'values': values, 'bonus': bonuses[kind][branch, author], 'total': sum(values) + bonuses[kind][branch, author]})
            totals = [a + b for a, b in zip(totals, values)]
        items = []
        for branch, employees in sorted(branches.items()):
            values = [sum(row['values'][i] for row in employees) for i in range(5)]
            bonus = sum(row['bonus'] for row in employees)
            items.append({'branch': branch, 'rows': employees, 'values': values, 'bonus': bonus, 'total': sum(values) + bonus})
        bonus = sum(item['bonus'] for item in items)
        sections.append({'kind': kind, 'branches': items, 'values': totals, 'bonus': bonus, 'total': sum(totals) + bonus})
    return sections


def workbook(sections):
    book = Workbook(); book.remove(book.active)
    for section in sections:
        policies = section['kind'] == 'Политики'
        sheet = book.create_sheet('1. Политики' if policies else '2. Продажи')
        labels = ['Подразделение'] + (['Сотрудник'] if policies else []) + PERIODS + ['Доплаты за кварталы', 'Итого']
        sheet.append(['Мотивация за 2025 год · ' + section['kind'] + ' · USD'])
        sheet.merge_cells(start_row=1, start_column=1, end_row=1, end_column=len(labels))
        sheet.append(labels)
        for branch in section['branches']:
            sheet.append([branch['branch']] + (['Итого по подразделению'] if policies else []) + branch['values'] + [branch['bonus'], branch['total']])
            subtotal = sheet.max_row
            if policies:
                for row in branch['rows']:
                    sheet.append([row['branch'], row['author'], *row['values'], row['bonus'], row['total']])
            for cell in sheet[subtotal]:
                cell.font = Font(bold=True, color='243B53'); cell.fill = PatternFill('solid', fgColor='F1EDFF')
        sheet.append(['Итого'] + ([''] if policies else []) + section['values'] + [section['bonus'], section['total']])
        for row in sheet.iter_rows():
            for cell in row:
                if isinstance(cell.value, str):
                    cell.data_type = 's'
                elif cell.row > 2:
                    cell.number_format = '#,##0.00 "USD"'
        for cell in sheet[2]:
            cell.font = Font(bold=True); cell.fill = PatternFill('solid', fgColor='EDE9FE')
        for index in range(1, len(labels) + 1):
            from openpyxl.utils import get_column_letter
            sheet.column_dimensions[get_column_letter(index)].width = 42 if policies and index == 2 else 26
        sheet.freeze_panes = 'C3' if policies else 'B3'
        sheet.auto_filter.ref = f'A2:{sheet.cell(2, len(labels)).column_letter}{max(2, sheet.max_row - 1)}'
    from .recalculation_2025_performance import append_sheets
    append_sheets(book, sections)
    output = BytesIO(); book.save(output); output.seek(0)
    return output
