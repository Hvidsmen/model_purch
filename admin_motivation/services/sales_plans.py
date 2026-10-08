"""Read-only DWH import and atomic, dated motivation calculation."""
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
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


def load_plan(scenario_id, connector=None):
    scenario = SalesPlanScenario.objects.get(pk=scenario_id)
    connection, cursor = (connector or connect_database)('vm-dwh', 'DataWH')
    try:
        cursor.execute(SQL, scenario.source_version)
        rows = cursor.fetchall()
        columns = result_columns(cursor.description) if rows else []
    finally:
        connection.close()
    if not rows:
        raise ValidationError('Выбранный план пуст. Ранее загруженные данные сохранены.')
    lines = []
    for row_number, row in enumerate(rows, start=1):
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
    # The output fallback can collapse NULL, empty and explicit OTHER groups.
    # Preserve all their amounts, both by subdivision and in the global plan.
    lines = aggregate_lines(lines)
    subdivision_count = len(lines)
    lines.extend(aggregate_lines(lines, global_plan=True))
    with transaction.atomic():
        locked = SalesPlanScenario.objects.select_for_update().get(pk=scenario_id)
        if locked.source_version != scenario.source_version:
            raise ValidationError('Источник сценария изменился во время загрузки.')
        locked.lines.all().delete()
        SalesPlanLine.objects.bulk_create(lines)
        locked.loaded_at, locked.calculated_at = timezone.now(), None
        locked.save(update_fields=['loaded_at', 'calculated_at'])
    return subdivision_count


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
def calculate_plan(scenario_id):
    scenario = SalesPlanScenario.objects.select_for_update().get(pk=scenario_id)
    lines = list(scenario.lines.all())
    if not lines:
        raise ValidationError('Сначала загрузите план продаж.')
    versions = list(GlobalCoeffVersion.objects.order_by('effective_from'))
    cells = {}
    for cell in SubdivisionCoeff.objects.select_related('subdivision', 'goods', 'type_coeff', 'segment'):
        key = (cell.version_id, cell.subdivision.subdivision_key, cell.goods.planning_group_sales,
               cell.goods.group, cell.goods.brand, cell.type_coeff.type_coeff_name, cell.segment.segment_name.upper().replace('K', 'O', 1))
        if key in cells:
            raise ValidationError('Найдены неоднозначные коэффициенты для одной классификации.')
        cells[key] = cell.motivation_coeff
    for cell in GlobalCoeff.objects.select_related('goods', 'type_coeff', 'segment'):
        key = (cell.version_id, '', cell.goods.planning_group_sales, cell.goods.group, cell.goods.brand,
               cell.type_coeff.type_coeff_name, cell.segment.segment_name.upper().replace('K', 'O', 1))
        if key in cells:
            raise ValidationError('Найдены неоднозначные глобальные коэффициенты.')
        cells[key] = cell.motivation_coeff
    for line in lines:
        version = next((v for v in reversed(versions) if v.effective_from <= line.plan_date), None)
        if version is None:
            raise ValidationError(f'Нет версии коэффициентов на {line.plan_date:%d.%m.%Y}.')
        result, totals = {}, []
        for kind in KINDS:
            details, total = {}, Decimal(0)
            for segment in SEGMENTS:
                key = (version.pk, line.subdivision, line.planning_group_sales, line.group, line.brand, kind, segment)
                if key not in cells:
                    raise ValidationError(f'Нет коэффициента: {line.subdivision}, {line.planning_group_sales}, {line.group}, {line.brand}, {kind}, {segment}; версия {version}.')
                coefficient = number(cells[key])
                if not -1 <= coefficient <= 1:
                    raise ValidationError('Коэффициент должен быть от -1 до 1.')
                usd = number(line.segment_amounts.get(segment))
                contribution = coefficient * usd
                details[segment] = {'coefficient': str(coefficient), 'usd': str(usd), 'motivation': str(contribution)}
                total += contribution
            result[kind] = details
            totals.append(total)
        line.coefficient_version = version
        line.calculation = result
        line.policies_usd, line.sales_usd = totals
        line.total_usd = sum(totals)
        for amount in [*totals, line.total_usd]:
            number(amount)
    SalesPlanLine.objects.bulk_update(lines, ['coefficient_version', 'calculation', 'policies_usd', 'sales_usd', 'total_usd'])
    scenario.calculated_at = timezone.now()
    scenario.save(update_fields=['calculated_at'])
    return len(lines)
