"""Read-only DWH import and atomic, dated motivation calculation."""
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from django.db import connection
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from ..conns import connect_database
from ..models import SalesPlanScenario, SalesPlanLine, GlobalCoeffVersion, SubdivisionCoeff, GlobalCoeff

SEGMENTS = [f'O{i}' for i in range(5)]
KINDS = ['Политики', 'Продажи']
SQL = (Path(__file__).resolve().parents[1] / 'sql' / 'sales_plan.sql').read_text(encoding='utf-8')


def number(value):
    try:
        result = Decimal(str(value))
        if not result.is_finite() or abs(result) >= Decimal('1e18'):
            raise ValueError()
        return result
    except (InvalidOperation, ValueError, TypeError):
        raise ValidationError('В плане обнаружена некорректная сумма.')


def source_versions(connector=None):
    connection, cursor = (connector or connect_database)('vm-dwh', 'DataWH')
    try:
        cursor.execute('SELECT DISTINCT CONVERT(nvarchar(255), Version_) FROM DataWH.planning.PlanSales WHERE Version_ IS NOT NULL ORDER BY 1')
        return [str(row[0]) for row in cursor.fetchall()]
    finally:
        connection.close()


def result_columns(description):
    """Read ODBC metadata, never infer meaning from a column's position."""
    columns = [str(column[0]).strip().casefold() for column in description or []]
    if 'brand' in columns and 'марка(бренд)' not in columns:
        columns[columns.index('brand')] = 'марка(бренд)'
    required = ['subdivision', 'planninggroupsaleserp', 'grouperp', 'марка(бренд)',
                'date_', 'amountusd', *[f'usd_o{i}' for i in range(5)]]
    missing = [name for name in required if name not in columns]
    if missing:
        raise ValidationError('В результате SQL отсутствуют столбцы: ' + ', '.join(missing) + '.')
    if len(set(columns)) != len(columns):
        raise ValidationError('В результате SQL повторяются названия столбцов.')
    return columns


def plan_date(value, row_number):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        text = value.strip()
        try:
            return date.fromisoformat(text)
        except ValueError:
            try:
                return datetime.fromisoformat(text.replace('Z', '+00:00')).date()
            except ValueError:
                pass
    raise ValidationError(f'Строка {row_number}: некорректная дата Date_={str(value)[:80]!r}. Ожидается дата YYYY-MM-DD.')


def classification(value, column, row_number):
    if column == 'planninggroupsaleserp' and (value is None or not str(value).strip()):
        return '9. OTHER'
    if value is None or not str(value).strip():
        raise ValidationError(f'Строка {row_number}: не заполнено поле {column}.')
    value = str(value).strip()
    if len(value) > 255:
        raise ValidationError(f'Строка {row_number}: поле {column} длиннее 255 символов.')
    return value


def load_plan(scenario_id, connector=None, progress=None):
    report(progress, 'Запрос плана и распределения сегментов в MS SQL', None)
    scenario = SalesPlanScenario.objects.get(pk=scenario_id)
    connection, cursor = (connector or connect_database)('vm-dwh', 'DataWH')
    try:
        cursor.execute(SQL, scenario.source_version)
        columns = result_columns(cursor.description)
        lines = []
        for row_number, row in enumerate(sql_rows(cursor, progress), start=1):
            if len(row) != len(columns):
                raise ValidationError(f'Строка {row_number}: число значений не соответствует столбцам SQL.')
            values = dict(zip(columns, row))
            on_date = plan_date(values['date_'], row_number)
            sub, pg, group, brand = [classification(values[column], column, row_number)
                                    for column in ['subdivision', 'planninggroupsaleserp', 'grouperp', 'марка(бренд)']]
            try:
                amounts = {segment: str(number(values[f'usd_{segment.lower()}'])) for segment in SEGMENTS}
                amount = number(values['amountusd'])
            except ValidationError as error:
                raise ValidationError(f'Строка {row_number} ({sub}, {pg}, {on_date}): ' + ' '.join(error.messages)) from error
            if abs(sum(map(Decimal, amounts.values())) - amount) > max(Decimal('0.0001'), abs(amount)*Decimal('0.00000001')):
                raise ValidationError(f'Строка {row_number} ({sub}, {pg}, {on_date}): сумма сегментов O0–O4 не совпадает с AmountUSD.')
            lines.append(SalesPlanLine(scenario_id=scenario_id, plan_date=on_date, subdivision=sub,
                planning_group_sales=pg, group=group, brand=brand, amount_usd=amount, segment_amounts=amounts))
    finally:
        connection.close()
    if not lines:
        raise ValidationError('Выбранный план пуст. Ранее загруженные данные сохранены.')
    # The output fallback can collapse NULL, empty and explicit OTHER groups.
    # Preserve all their amounts, both by subdivision and in the global plan.
    report(progress, "Объединение строк и построение глобального плана", 65)
    lines = aggregate_lines(lines)
    subdivision_count = len(lines)
    lines.extend(aggregate_lines(lines, global_plan=True))
    with transaction.atomic():
        locked = SalesPlanScenario.objects.select_for_update().get(pk=scenario_id)
        if locked.source_version != scenario.source_version:
            raise ValidationError('Источник сценария изменился во время загрузки.')
        locked.lines.all().delete()
        for offset in range(0, len(lines), 1000):
            SalesPlanLine.objects.bulk_create(lines[offset:offset + 1000], batch_size=1000)
            report(progress, 'Сохранение плана', 75 + 24 * min(offset + 1000, len(lines)) / len(lines), min(offset + 1000, len(lines)), len(lines))
        locked.loaded_at, locked.calculated_at = timezone.now(), None
        locked.save(update_fields=['loaded_at', 'calculated_at'])
    return subdivision_count


def sql_rows(cursor, progress):
    processed = 0
    while True:
        batch = cursor.fetchmany(2000)
        if not batch:
            return
        for row in batch:
            yield row
        processed += len(batch)
        report(progress, 'Чтение и проверка строк MS SQL', None, processed)


def aggregate_lines(lines, global_plan=False):
    aggregates = {}
    for line in lines:
        sub = '' if global_plan else line.subdivision
        key = (sub, line.plan_date, line.planning_group_sales, line.group, line.brand)
        if key not in aggregates:
            aggregates[key] = SalesPlanLine(scenario_id=line.scenario_id, plan_date=line.plan_date, subdivision=sub,
                planning_group_sales=line.planning_group_sales, group=line.group, brand=line.brand,
                amount_usd=Decimal(0), segment_amounts={segment: '0' for segment in SEGMENTS})
        total = aggregates[key]
        total.amount_usd += line.amount_usd
        for segment in SEGMENTS:
            total.segment_amounts[segment] = str(Decimal(total.segment_amounts[segment]) + Decimal(line.segment_amounts[segment]))
    for total in aggregates.values():
        number(total.amount_usd)
        for amount in total.segment_amounts.values():
            number(amount)
    return list(aggregates.values())


@transaction.atomic
def calculate_plan(scenario_id, progress=None):
    scenario = SalesPlanScenario.objects.select_for_update().get(pk=scenario_id)
    total_lines = scenario.lines.count()
    if not total_lines:
        raise ValidationError('Сначала загрузите план продаж.')
    report(progress, 'Подготовка коэффициентов', 5, 0, total_lines)
    versions = list(GlobalCoeffVersion.objects.order_by('effective_from'))
    cells = {}
    fields = ['version_id', 'goods__planning_group_sales', 'goods__group', 'goods__brand',
              'type_coeff__type_coeff_name', 'segment__segment_name', 'motivation_coeff']
    for model in [SubdivisionCoeff, GlobalCoeff]:
        field_names = [fields[0], 'subdivision__subdivision_key', *fields[1:]] if model is SubdivisionCoeff else fields
        for row in model.objects.values_list(*field_names).iterator(chunk_size=2000):
            row = row if model is SubdivisionCoeff else (row[0], '', *row[1:])
            key = (*row[:6], row[6].upper().replace('K', 'O', 1))
            if key in cells:
                raise ValidationError('Найдены неоднозначные коэффициенты для одной классификации.')
            cells[key] = number(row[7])
    version_names = {version.pk: str(version) for version in versions}
    minimums, table_minimums = {}, {}
    for key, value in cells.items():
        version_id, subdivision, planning_group, group, brand, kind, segment = key
        if kind not in KINDS or segment not in SEGMENTS:
            continue
        coefficient = number(value)
        if not -1 <= coefficient <= 1:
            raise ValidationError('Коэффициент должен быть от -1 до 1.')
        scope = (version_id, subdivision, planning_group, kind)
        if scope not in minimums or coefficient < minimums[scope][0]:
            minimums[scope] = (coefficient, key)
        table_scope = (bool(subdivision), kind)
        if table_scope not in table_minimums or coefficient < table_minimums[table_scope][0]:
            table_minimums[table_scope] = (coefficient, key)
    version_cache, coefficient_cache = {}, {}
    pending, processed = [], 0
    lines = scenario.lines.order_by('pk').iterator(chunk_size=1000)
    for line in lines:
        if line.plan_date not in version_cache:
            version_cache[line.plan_date] = next((v for v in reversed(versions) if v.effective_from <= line.plan_date), None)
        version = version_cache[line.plan_date]
        # The selected version and classification repeat across plan periods.
        cache_key = (version.pk if version else None, line.subdivision, line.planning_group_sales, line.group, line.brand)
        version_id = version.pk if version else None
        result, totals = {}, []
        for kind in KINDS:
            details, total = {}, Decimal(0)
            for segment in SEGMENTS:
                lookup = (cache_key, kind, segment)
                cached = coefficient_cache.get(lookup)
                if cached is None:
                    key = (version_id, line.subdivision, line.planning_group_sales, line.group, line.brand, kind, segment)
                    fallback = key not in cells
                    source_key = key
                    source = 'exact'
                    if fallback:
                        source = 'group_minimum'
                        candidate = minimums.get((version_id, line.subdivision, line.planning_group_sales, kind))
                        if candidate is None:
                            source = 'table_minimum'
                            candidate = table_minimums.get((bool(line.subdivision), kind))
                        if candidate is None:
                            raise ValidationError(f'Нет коэффициента и значений для подстановки минимума: {line.subdivision or "Глобальный план"}, {line.planning_group_sales}, {kind}; версия {version}.')
                        coefficient, source_key = candidate
                    else:
                        coefficient = cells[key]
                    if not -1 <= coefficient <= 1:
                        raise ValidationError('Коэффициент должен быть от -1 до 1.')
                    metadata = {'coefficient': str(coefficient), 'source': source,
                                'source_version_id': source_key[0], 'source_version': version_names[source_key[0]],
                                'source_subdivision': source_key[1], 'source_planning_group': source_key[2],
                                'source_group': source_key[3], 'source_brand': source_key[4], 'source_segment': source_key[6]}
                    if len(coefficient_cache) >= 10000:
                        coefficient_cache.clear()
                    cached = coefficient_cache[lookup] = (coefficient, metadata)
                coefficient, metadata = cached
                usd = number(line.segment_amounts.get(segment))
                contribution = coefficient * usd
                details[segment] = {**metadata, 'usd': str(usd), 'motivation': str(contribution)}
                total += contribution
            result[kind] = details
            totals.append(total)
        line.coefficient_version = version
        line.calculation = result
        line.policies_usd, line.sales_usd = totals
        line.total_usd = sum(totals)
        for amount in [*totals, line.total_usd]:
            number(amount)
        pending.append(line)
        processed += 1
        if len(pending) == 1000:
            save_calculations(pending)
            pending.clear()
            report(progress, 'Расчёт и сохранение мотивации', 10 + 89 * processed / total_lines, processed, total_lines)
    if pending:
        save_calculations(pending)
    report(progress, 'Завершение транзакции', 99, processed, total_lines)
    scenario.calculated_at = timezone.now()
    scenario.save(update_fields=['calculated_at'])
    return processed



def report(callback, stage, percent=None, processed=None, total=None):
    if callback:
        callback({'stage': stage, 'percent': round(percent, 1) if percent is not None else None,
                  'processed': processed, 'total': total})


def save_calculations(lines):
    """Avoid constructing thousands of ORM CASE expression trees for large JSON rows."""
    names = ['coefficient_version', 'calculation', 'policies_usd', 'sales_usd', 'total_usd']
    fields = [SalesPlanLine._meta.get_field(name) for name in names]
    quote = connection.ops.quote_name
    table = quote(SalesPlanLine._meta.db_table)
    assignments = ', '.join(f'{quote(field.column)} = %s' for field in fields)
    params = []
    for line in lines:
        values = [field.get_db_prep_save(getattr(line, field.attname), connection) for field in fields]
        params.append([*values, line.pk, line.scenario_id])
    with connection.cursor() as cursor:
        cursor.executemany(f'UPDATE {table} SET {assignments} WHERE {quote("id")} = %s AND {quote("scenario_id")} = %s', params)
