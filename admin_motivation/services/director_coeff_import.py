"""Import blocks of director names and K0–K4 from a matrix workbook."""
from collections import defaultdict
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from io import BytesIO
import unicodedata
from zipfile import BadZipFile, ZipFile

from django.core.exceptions import ValidationError
from django.db import transaction
from openpyxl import load_workbook

from ..models import DirectorEmployee, Goods, SubdivisionDirectorCoeff
from .directors import KEYS, lock_editable


def label(value):
    return ' '.join(unicodedata.normalize('NFC', str(value or '')).replace('\\n', ' ').split())


def identity(value):
    return label(value).casefold()


def coefficient(value):
    if isinstance(value, bool) or value is None:
        raise ValueError
    text = str(value).strip().replace(',', '.')
    result = Decimal(text.rstrip('%')) / (100 if text.endswith('%') else 1)
    if not result.is_finite() or not -1 <= result <= 1:
        raise ValueError
    return result.quantize(Decimal('.000001'), rounding=ROUND_HALF_UP)


def read_director_blocks(upload):
    if not upload or not upload.name.lower().endswith('.xlsx'):
        raise ValidationError('Выберите файл Excel .xlsx.')
    if upload.size > 5 * 1024 * 1024:
        raise ValidationError('Максимальный размер файла — 5 МБ.')
    raw = upload.read()
    book = None
    try:
        with ZipFile(BytesIO(raw)) as archive:
            if sum(item.file_size for item in archive.infolist()) > 30 * 1024 * 1024:
                raise ValidationError('Файл слишком большой после распаковки.')
        book = load_workbook(BytesIO(raw), read_only=True, data_only=False)
        if 'Коэффициенты' not in book.sheetnames:
            raise ValidationError('В файле нужен лист «Коэффициенты».')
        sheet = book['Коэффициенты']
        if sheet.max_row > 5000 or sheet.max_column > 200:
            raise ValidationError('Допускается до 5000 строк и 200 столбцов.')
        rows = list(sheet.iter_rows(values_only=True))
        header = next((index for index, row in enumerate(rows[:50])
                       if len(row) >= 4 and label(row[1]) == 'Группа планов продаж'
                       and label(row[2]) in ('Группа ERP', 'Группа товаров') and label(row[3]) == 'Марка / бренд'), None)
        if header is None:
            raise ValidationError('Не найдена шапка товарной матрицы.')
        segments = next((index for index in range(header, min(header + 3, len(rows)))
                         if [label(value).upper() for value in rows[index][4:9]] == ['K0', 'K1', 'K2', 'K3', 'K4']), None)
        if segments is None:
            raise ValidationError('После шапки нужны блоки K0–K4.')
        blocks = []
        errors = []
        for column in range(4, len(rows[segments]) - 4, 5):
            if [label(value).upper() for value in rows[segments][column:column + 5]] != ['K0', 'K1', 'K2', 'K3', 'K4']:
                continue
            names = list({identity(row[column]): label(row[column]) for row in rows[:header] if len(row) > column and label(row[column])}.values())
            entries, seen = [], set()
            for row_number, row in enumerate(rows[segments + 1:], segments + 2):
                raw_values = row[column:column + 5]
                if not any(value is not None and str(value).strip() for value in raw_values):
                    continue
                matrix = tuple(identity(value) for value in row[1:4])
                if len(matrix) != 3 or not all(matrix):
                    errors.append(f'Строка {row_number}: заполните группу планов, группу ERP и марку.')
                    continue
                if matrix in seen:
                    errors.append(f'Строка {row_number}: товарная строка повторяется в блоке.')
                seen.add(matrix)
                try:
                    values = tuple(coefficient(value) for value in raw_values)
                    if len(values) != 5:
                        raise ValueError
                except (ValueError, InvalidOperation):
                    errors.append(f'Строка {row_number}, блок {column + 1}: заполните все K0–K4 числами от -1 до 1. Формулы сохраните как значения.')
                    continue
                entries.append((matrix, values, row_number))
            if entries and not names:
                errors.append(f'Над блоком со столбца {column + 1} нет ФИО директоров.')
            if entries:
                blocks.append((names, entries))
        if errors:
            raise ValidationError(errors[:30])
        if not blocks:
            raise ValidationError('В файле нет заполненных блоков коэффициентов директоров.')
        return blocks
    except ValidationError:
        raise
    except Exception as error:
        raise ValidationError('Не удалось прочитать Excel. Выберите корректный файл .xlsx.') from error
    finally:
        if book:
            book.close()


def import_director_coefficients(upload, version_id):
    blocks = read_director_blocks(upload)
    with transaction.atomic():
        version = lock_editable(version_id)
        employees, goods = defaultdict(list), defaultdict(list)
        for employee in DirectorEmployee.objects.prefetch_related('subdivisions'):
            employees[identity(employee.full_name)].append(employee)
        for good in Goods.objects.all():
            goods[tuple(identity(value) for value in (good.planning_group_sales, good.group, good.brand))].append(good)
        errors, prepared, seen, imported_employees = [], {}, set(), set()
        for names, entries in blocks:
            selected = []
            for name in names:
                key = identity(name)
                if key in seen:
                    errors.append(f'Директор «{name}» указан в нескольких блоках.')
                    continue
                seen.add(key)
                matches = employees[key]
                if len(matches) != 1:
                    errors.append(f'ФИО «{name}»: ' + ('директор не найден.' if not matches else 'найдено несколько сотрудников.'))
                    continue
                employee = matches[0]
                subdivisions = list(employee.subdivisions.all())
                if not subdivisions:
                    errors.append(f'У директора «{name}» нет привязанных подразделений.')
                    continue
                selected.append((employee, subdivisions))
            for matrix, values, row_number in entries:
                matches = goods[matrix]
                if len(matches) != 1:
                    errors.append(f'Строка {row_number}: товарная матрица «{" / ".join(matrix)}» не найдена или неоднозначна.')
                    continue
                for employee, subdivisions in selected:
                    imported_employees.add(employee.pk)
                    for subdivision in subdivisions:
                        prepared[subdivision.pk, employee.pk, matches[0].pk] = values
        if errors:
            raise ValidationError(list(dict.fromkeys(errors))[:30])
        existing = {(row.subdivision_id, row.employee_id, row.goods_id): row
                    for row in SubdivisionDirectorCoeff.objects.filter(version=version, employee_id__in=imported_employees)}
        created, updated = [], []
        for (sub_id, employee_id, goods_id), values in prepared.items():
            row = existing.get((sub_id, employee_id, goods_id))
            if row and tuple(getattr(row, key) for key in KEYS) == values:
                continue
            if row is None:
                row = SubdivisionDirectorCoeff(version=version, subdivision_id=sub_id, employee_id=employee_id, goods_id=goods_id)
                created.append(row)
            else:
                updated.append(row)
            for key, value in zip(KEYS, values):
                setattr(row, key, value)
        SubdivisionDirectorCoeff.objects.bulk_create(created, batch_size=100)
        SubdivisionDirectorCoeff.objects.bulk_update(updated, KEYS, batch_size=100)
        return {'directors': len(imported_employees), 'created': len(created), 'updated': len(updated),
                'unchanged': len(prepared) - len(created) - len(updated)}
