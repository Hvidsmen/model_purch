"""Role-based preview reports. Legacy reports remain independent."""
from collections import defaultdict
from contextlib import contextmanager
from decimal import Decimal
from django.core.exceptions import PermissionDenied, ValidationError
from base.access import role_for, ADMIN, DIRECTOR, FINANCIER, BRANCH_DIRECTOR, DEPUTY_DIRECTOR, PERSONAL_ROLES, REPORT_ROLES
from base.report_access import politician_scope, scoped_values
from ..models import GlobalCoeffVersion, SalesPlanScenario, Goods, Subdivision, DirectorEmployee
from .reports import load_report, number

CATALOG = {
    'mine': ('Моя премия', 'Своя премия, доплаты и объяснение расчёта.'),
    'team': ('Премии команды', 'Премии сотрудников доступных подразделений.'),
    'registry': ('Реестр премий к выплате', 'Рассчитанные суммы по сотрудникам и периодам для проверки финансистом.'),
    'executive': ('Сводный отчёт: план премий', 'Глобальные коэффициенты и коэффициенты подразделений на одинаковом плане.'),
    'detail': ('Детальный расчёт', 'Отгрузки, ставки K0–K4, условия выполнения и итоговая премия.'),
}
PERIODS = [('all', 'Все доступные периоды'), ('1', '1 квартал'), ('2', '2 квартал'), ('3', '3 квартал'), ('4', '4 квартал'), ('5', 'Год')]


def available(user):
    role = role_for(user, 'motivation')
    keys = ['detail']
    if role in PERSONAL_ROLES:
        keys.insert(0, 'mine')
    if role not in PERSONAL_ROLES:
        keys.insert(0, 'team')
    if role in (ADMIN, FINANCIER):
        keys.append('registry')
    if role not in REPORT_ROLES:
        keys.append('executive')
    return {key: CATALOG[key] for key in keys}


def period_code(label):
    name = str(label or '').strip().casefold()
    if ' · ' in name:
        name = name.split(' · ', 1)[1]
    return '5' if name == 'год' else name[0] if name and name[:1] in '1234' else ''


def restrict(user, query):
    scope = politician_scope(user)
    if role_for(user, 'motivation') in REPORT_ROLES and not scope:
        raise PermissionDenied
    allowed = scope.get('type', []) if scope else ['Политики', 'Продажи', 'Директора']
    allowed = [allowed] if isinstance(allowed, str) else allowed
    selected = query.get('type', '')
    if selected and selected not in allowed:
        raise PermissionDenied
    period = query.get('period', 'all')
    if period not in dict(PERIODS):
        raise ValidationError('Выберите период премии.')
    if scope and scope.get('pilot_periods') and period not in ('all', '4', '5'):
        raise PermissionDenied
    return scope, [selected] if selected else allowed, period


@contextmanager
def current_run(execution, connector=None):
    """The result table is replaceable: never label it as an arbitrary past run."""
    from ..conns import connect_database
    connection, cursor, locked = None, None, False
    try:
        connection, cursor = (connector or connect_database)('vm-dwh', 'DataWH')
        connection.timeout = 30
        cursor.execute("DECLARE @r int; EXEC @r=sys.sp_getapplock @Resource=N'PortalMotivationCalculation',@LockMode=N'Shared',@LockOwner=N'Session',@LockTimeout=0; SELECT @r;")
        result = cursor.fetchone()
        if not result or result[0] < 0:
            raise ValidationError('Выполняется расчёт. Дождитесь завершения и повторите запрос.')
        locked = True
        cursor.execute('SELECT TOP (1) RunId,State_ FROM motivation.PortalMotivationRun ORDER BY StartedAt DESC')
        latest = cursor.fetchone()
        if not latest or str(latest[0]).casefold() != str(execution.run_id).casefold() or latest[1] != 'completed':
            raise ValidationError('Таблица результатов относится к другому или незавершённому запуску. Выберите последний завершённый расчёт; архивные суммы пока не сохраняются.')
        yield
    finally:
        if connection is not None:
            try:
                if locked:
                    cursor.execute("EXEC sys.sp_releaseapplock @Resource=N'PortalMotivationCalculation',@LockOwner=N'Session';")
            finally:
                connection.close()


def actual_reports(user, execution, query, *, detailed=False):
    scope, kinds, period = restrict(user, query)
    expected_year = str(execution.parameters.get('fact_year', ''))
    year = query.get('year', expected_year)
    if year != expected_year:
        raise ValidationError('Год должен совпадать с годом выбранного расчёта.')
    currency = query.get('currency', 'USD')
    if currency not in ('USD', 'RUB'):
        raise ValidationError('Выберите USD или RUB.')
    results, ledger = [], []
    for kind in kinds:
        values = {'TypeCoeff_': kind, 'Subdivision': query.getlist('subdivision'), 'DealAuthor': query.getlist('author'),
                  'gp_sales': [], 'GPGoods': [], 'Brend': [], 'Years': [year], 'Currency': currency}
        if scope:
            values = scoped_values('manager-detail', values, scope)
        data = load_report('manager-detail', values, access_scope=scope, premium_period=period, summary_only=not detailed)
        if detailed:
            results.append({'kind': kind, 'data': data})
        summary = data['period_summary']
        for item in summary['rows']:
            for index, column in enumerate(summary['columns']):
                amount = item['values'][index]
                if amount is None:
                    continue
                ledger.append({'subdivision': item['subdivision'], 'author': item['author'], 'unallocated': item.get('unallocated', False),
                    'kind': kind, 'period': column['label'], 'code': period_code(column['label']), 'amount': amount,
                    'bonus': Decimal(0), 'currency': currency})
            # Annual remainder is a separate component, never counted twice.
            if item['remainder']:
                ledger.append({'subdivision': item['subdivision'], 'author': item['author'], 'unallocated': item.get('unallocated', False),
                    'kind': kind, 'period': 'Доплата за кварталы', 'code': '5', 'amount': Decimal(0),
                    'bonus': item['remainder'], 'currency': currency})
    for item in ledger:
        item['total'] = item['amount'] + item['bonus']
    return {'has_unallocated': any(row['unallocated'] for row in ledger), 'sections': results, 'rows': ledger, 'total': sum((item['total'] for item in ledger), Decimal(0)), 'currency': currency}


def frozen_versions(execution):
    snapshot = execution.export_snapshot
    entries = snapshot.get('versions', [])
    if not entries:
        raise ValidationError('В запуске не сохранены версии коэффициентов. Сравнение по текущим ставкам вместо исторических запрещено.')
    versions = list(GlobalCoeffVersion.objects.filter(pk__in=[entry['id'] for entry in entries]).order_by('effective_from', 'pk'))
    expected = {entry['id']: entry for entry in entries}
    if len(versions) != len(entries) or any(version.revision != expected[version.pk]['revision'] for version in versions):
        raise ValidationError('Коэффициенты этого запуска изменены или удалены. Для достоверного сравнения нужен неизменённый набор.')
    for version in versions:
        if version.effective_from.isoformat() != expected[version.pk]['from']:
            raise ValidationError('Изменились даты версий расчёта.')
    # Reference edits also affect join keys; do not silently reinterpret history.
    current_goods = {row.pk: row for row in Goods.objects.filter(pk__in=[row['id'] for row in snapshot.get('goods', [])])}
    for item in snapshot.get('goods', []):
        row = current_goods.get(item['id'])
        if row is None or any(getattr(row, field) != item[field] for field in ('goods_key', 'planning_group_sales', 'group', 'brand')):
            raise ValidationError('Классификация товаров изменилась после расчёта.')
    from ..models import TypeCoeff, SegmentCoeff, VariationCalculate, KindManagerCoeff
    for name, model in [('types', TypeCoeff), ('segments', SegmentCoeff), ('formulas', VariationCalculate), ('periods', KindManagerCoeff)]:
        current = {row['id']: row for row in model.objects.values()}
        for item in snapshot.get(name, []):
            if current.get(item['id']) != item:
                raise ValidationError('Справочники коэффициентов изменились после расчёта.')
    branches = dict(Subdivision.all_objects.values_list('pk', 'subdivision_key'))
    if any(branches.get(item['id']) != item['subdivision_key'] for item in snapshot.get('branches', [])):
        raise ValidationError('Подразделения изменились после расчёта.')
    from .subdivision_aliases import rules
    if sorted([list(row) for row in snapshot.get('aliases', [])]) != sorted([list(row) for row in rules()]):
        raise ValidationError('Объединения подразделений изменились после расчёта.')
    return versions


def default_plan(execution):
    return SalesPlanScenario.objects.filter(is_deleted=False, source_version=execution.parameters.get('plan_version', ''),
        loaded_at__isnull=False, loaded_at__lte=execution.started_at).order_by('-loaded_at', '-pk').first()


def planned_budget(execution, scenario, query):
    """Same plan amounts, employees and dated coefficients on both sides."""
    from .comparison import Index, coefficient_rows
    from .director_comparison import DirectorIndex
    from .plan_summary import empty_bucket, node, add
    from .subdivision_aliases import mapping, canonical
    from .subdivision_scope import included
    versions = frozen_versions(execution)
    year = int(query.get('year', execution.parameters['fact_year']))
    period = query.get('period', '5')
    if period not in dict(PERIODS):
        raise ValidationError('Выберите период премии.')
    kinds = {'Политики': 'policies', 'Продажи': 'sales', 'Директора': 'directors'}
    selected = query.get('type', '')
    if selected and selected not in kinds:
        raise ValidationError('Выберите вид мотивации.')
    included_kinds = [kinds[selected]] if selected else list(kinds.values())
    ids = [version.pk for version in versions]
    indexes = {side: Index(coefficient_rows(ids, subdivision=local)) for side, local in [('global', False), ('local', True)]}
    directors = {side: DirectorIndex(ids, local=local) for side, local in [('global', False), ('local', True)]}
    lines = scenario.lines.exclude(subdivision='').filter(plan_date__year=year).order_by()
    if period in ('1', '2', '3', '4'):
        quarter = int(period)
        lines = lines.filter(plan_date__month__gte=quarter * 3 - 2, plan_date__month__lte=quarter * 3)
    aliases, buckets = mapping(), {}
    quarter_share = number(execution.parameters.get('quarter_share'))
    annual_share = number(execution.parameters.get('year_share'))
    if quarter_share < 0 or annual_share < 0 or quarter_share + annual_share != 1:
        raise ValidationError('В запуске нет корректного распределения квартальной и годовой премии.')
    share = annual_share if period == '5' else quarter_share if period != 'all' else Decimal(1)
    selected_subdivisions = query.getlist('subdivision')
    for day, original, pg, group, brand, amount, segments in lines.values_list('plan_date', 'subdivision', 'planning_group_sales', 'group', 'brand', 'amount_usd', 'segment_amounts').iterator(chunk_size=2000):
        sub = canonical(original, aliases)
        if not included(original, aliases) or selected_subdivisions and sub not in selected_subdivisions:
            continue
        version = next((version for version in reversed(versions) if version.effective_from <= day), None)
        if version is None:
            raise ValidationError('В плане есть даты вне сохранённой цепочки коэффициентов расчёта.')
        if any(f'O{i}' not in segments for i in range(5)):
            raise ValidationError('План не содержит распределение по ступеням O0–O4.')
        amounts = [number(segments[f'O{i}']) for i in range(5)]
        bucket = buckets.setdefault(sub, empty_bucket())
        bucket['plan'] += amount
        bucket['ddp'] = None
        for kind, target in kinds.items():
            if target not in included_kinds or target == 'directors':
                continue
            for side in ('global', 'local'):
                matrix, _ = indexes[side].coefficients(version.pk, sub if side == 'local' else '', pg, group, brand, kinds=[kind])
                value = sum((rate * usd for rate, usd in zip(matrix[0], amounts)), Decimal(0)) * share
                bucket[side][target] += value
        if 'directors' in included_kinds:
            staff = directors['local'].by_employee(version.pk, sub, pg, group, brand, amounts)
            global_amount = directors['global'].motivation(version.pk, '', pg, group, brand, amounts) * share
            bucket['global']['directors'] += global_amount * len(staff)
            bucket['local']['directors'] += sum(staff.values(), Decimal(0)) * share
            for employee, value in staff.items():
                entry = bucket['staff'].setdefault(employee, {'plan': Decimal(0), 'ddp': None, 'global': Decimal(0), 'local': Decimal(0)})
                entry['plan'] += amount
                entry['global'] += global_amount
                entry['local'] += value * share
    company = empty_bucket()
    company['ddp'] = None
    for bucket in buckets.values():
        company['plan'] += bucket['plan']
        for side in ('global', 'local'):
            for kind in included_kinds:
                company[side][kind] += bucket[side][kind]
    names = {item['employee_id']: item['employee__full_name'] for item in execution.export_snapshot.get('employees', [])}
    result = [node('Компания', company, names, included_kinds)] if buckets else []
    result += [node(sub, bucket, names, included_kinds) for sub, bucket in sorted(buckets.items())]
    def differences(nodes):
        for item in nodes:
            item['difference_percent'] = item['difference'] / abs(item['global']) if item['global'] else Decimal(0) if not item['difference'] else None
            differences(item.get('children', []))
    differences(result)
    return result
