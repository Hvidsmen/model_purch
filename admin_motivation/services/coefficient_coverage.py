"""Read-only coverage of a loaded plan or actual shipments by exact local settings."""
from collections import defaultdict
from datetime import date
from decimal import Decimal
from django.core.exceptions import ValidationError
from ..models import (GlobalCoeffVersion, GlobalCoeff, GlobalDirectorCoeff, SubdivisionCoeff, SubdivisionDirectorCoeff, DirectorEmployee, Subdivision)
from .pilot_formula import GLOBAL_SUBDIVISION
from .subdivision_aliases import coefficient_rows, mapping, canonical, expand
from .subdivision_scope import included, sql_predicate
from .reports import number, fetch


def key(value):
    return str(value or '').rstrip().casefold()


def legacy_formula(pg):
    return 'Группа планов продаж' if key(pg) in {'1. rac', '2. vrf', '3. prof'} else 'Подразделение'


def plan_rows(plan, subdivision=''):
    names = mapping()
    # Calculated lines contain large JSON payloads. Coverage only needs the key
    # and amount: never deserialize calculation/segment_amounts or sort the plan.
    lines = plan.lines.exclude(subdivision='').order_by()
    if subdivision:
        lines = lines.filter(subdivision__in=expand(subdivision, names))
    for row in lines.values('subdivision', 'plan_date', 'planning_group_sales', 'group', 'brand', 'amount_usd').iterator(chunk_size=2000):
        branch = canonical(row['subdivision'], names)
        if included(branch) and (not subdivision or key(branch) == key(canonical(subdivision, names))):
            yield {'subdivision': branch, 'date': row['plan_date'], 'planning': row['planning_group_sales'],
                   'group': row['group'], 'brand': row['brand'], 'amount': row['amount_usd'], 'documents': 0}


def fact_rows(year, subdivision='', connector=None):
    from ..conns import connect_database
    if type(year) is not int or not 1900 <= year <= 9998:
        raise ValidationError('Укажите год факта от 1900 до 9998.')
    names = mapping()
    condition, args = '', [date(year, 1, 1), date(year + 1, 1, 1)]
    if subdivision:
        branches = expand(subdivision, names)
        condition = ' AND sub.SubdivisionName IN (' + ','.join('?' for _ in branches) + ')'
        args.extend(branches)
    # These are the calculation's exact coefficient-join keys, not display labels.
    pg = 'p.PlanningGroupSalesErp'
    group = "CASE WHEN p.PlanningGroupSalesErp IN (N'1. RAC',N'2. VRF',N'3. PROF') THEN p.GroupERP ELSE N'*' END"
    brand = "CASE WHEN p.GroupERP IN (N'Бытовые кондиционеры',N'Полупромышленные кондиционеры',N'Системы мульти-сплит',N'Системы VRF') THEN p.BrandName ELSE N'' END"
    connection = None
    try:
        connection, cursor = (connector or connect_database)('vm-dwh', 'DataWH')
        connection.timeout = 60
        cursor.execute("SELECT OBJECT_DEFINITION(OBJECT_ID(N'motivation.sp_CalcBySub')),OBJECT_ID(N'motivation.fn_CoeffGroupERP'),OBJECT_ID(N'motivation.fn_CoeffBrand')")
        definition, group_function, brand_function = cursor.fetchone()
        if not definition:
            raise ValidationError('Нет доступа к определению расчёта. Невозможно достоверно определить ключи классификации факта.')
        import re
        from .pilot_formula import mask_sql
        code = mask_sql(definition)
        if re.search(r'\bfn_CoeffGroupERP\b', code, re.I):
            if not group_function:
                raise ValidationError('В DWH не найдена функция классификации группы коэффициентов.')
            group = 'motivation.fn_CoeffGroupERP(p.PlanningGroupSalesErp,p.GroupERP)'
        if re.search(r'\bfn_CoeffBrand\b', code, re.I):
            if not brand_function:
                raise ValidationError('В DWH не найдена функция классификации бренда коэффициентов.')
            brand = 'motivation.fn_CoeffBrand(p.GroupERP,p.BrandName)'
        sql = f"""SELECT sub.SubdivisionName AS subdivision,CONVERT(date,s.ClosedDealDate) AS date,
    {pg} AS planning,{group} AS [group],{brand} AS brand,
    SUM(s.Price-COALESCE(s.AmountUSDIt_ITW,0)+COALESCE(s.BallsOuterUSD,0)) AS amount,COUNT_BIG(*) AS documents
    FROM dbo.Sales s INNER JOIN dbo.Product p ON p.ProductCode=s.ProductCode
    INNER JOIN dbo.Subdivisions sub ON sub.SubdivisionKey=s.Subdivision
    WHERE s.ClosedDealDate>=? AND s.ClosedDealDate<? AND p.[Тип номенклатуры]=N'Товар'
    AND {sql_predicate('sub.SubdivisionName')}{condition}
    GROUP BY sub.SubdivisionName,CONVERT(date,s.ClosedDealDate),{pg},{group},{brand}"""
        rows = fetch(cursor, sql, args, limit=50000)
        for row in rows:
            row['subdivision'] = canonical(row['subdivision'], names)
        return [row for row in rows if included(row['subdivision'])]
    finally:
        if connection is not None:
            connection.close()


def check(version, rows, kind='', scope='subdivision'):
    if scope not in ('global', 'subdivision'):
        raise ValidationError('Выберите область проверки.')
    global_scope = scope == 'global'
    approved = list(GlobalCoeffVersion.objects.filter(status='approved').order_by('effective_from', 'pk'))
    timeline = approved if version.status == 'approved' else [item for item in approved if item.effective_from < version.effective_from] + [version]
    if version.status == 'approved' and all(item.pk != version.pk for item in timeline):
        timeline.append(version)
    ids = [item.pk for item in timeline]
    versions = {item.pk: item for item in timeline}
    index = defaultdict(lambda: {'segments': set(), 'formulas': set()})
    # Flat tuples avoid materializing every coefficient and five related model
    # instances in the request worker. Do not populate QuerySet result caches.
    ordinary = (GlobalCoeff if global_scope else SubdivisionCoeff).objects.filter(version_id__in=ids)
    if kind:
        ordinary = ordinary.filter(type_coeff__type_coeff_name=kind)
    fields = ['version_id', 'goods__planning_group_sales', 'goods__group', 'goods__brand',
              'type_coeff__type_coeff_name', 'segment__segment_name', 'variation_calculate__variation_name']
    if not global_scope:
        fields.insert(1, 'subdivision__subdivision_key')
    ordinary_values = ordinary.order_by().values_list(*fields).iterator(chunk_size=2000)
    if global_scope:
        ordinary_values = ((row[0], '', *row[1:]) for row in ordinary_values)
    raw = ((v, branch, pg, group, brand, employee_kind.strip(), 0, segment.upper().replace('O', 'K'), formula)
           for v, branch, pg, group, brand, employee_kind, segment, formula in ordinary_values)
    for row in coefficient_rows(raw, 1, range(8)):
        identity = (row[0], *map(key, row[1:6]), row[6])
        index[identity]['segments'].add(row[7])
        index[identity]['formulas'].add(row[8])
    names = mapping()
    director_names, staff = {}, defaultdict(set)
    director_rows = (GlobalDirectorCoeff if global_scope else SubdivisionDirectorCoeff).objects.filter(version_id__in=ids)
    if kind and kind != 'Директора':
        director_rows = director_rows.none()
    fields = ['version_id', 'goods__planning_group_sales', 'goods__group', 'goods__brand',
              'variation_calculate__variation_name']
    if not global_scope:
        fields.insert(1, 'subdivision__subdivision_key')
        fields[-1:-1] = ['employee_id', 'employee__full_name']
    director_values = director_rows.order_by().values_list(*fields).iterator(chunk_size=2000)
    if global_scope:
        director_values = ((row[0], '', *row[1:4], 0, '', row[4]) for row in director_values)
    for v, branch, pg, group, brand, employee, full_name, formula in director_values:
        branch = canonical(branch, names)
        if not included(branch):
            continue
        identity = (v, key(branch), key(pg), key(group), key(brand), key('Директора'), employee)
        index[identity]['segments'].update(f'K{i}' for i in range(5))
        index[identity]['formulas'].add(formula or versions[v].fallback_variation or legacy_formula(pg))
        director_names[employee] = full_name
        staff[key(branch)].add(employee)
    names = mapping()
    for employee in DirectorEmployee.objects.filter(is_archived=False).prefetch_related('subdivisions'):
        director_names[employee.pk] = employee.full_name
        for branch in employee.subdivisions.all():
            staff[key(canonical(branch.subdivision_key, names))].add(employee.pk)
    branch_ids = {key(row.subdivision_key): row.pk for row in Subdivision.objects.all()}
    grouped = {}
    examined = set()
    source_count = 0
    kinds = [kind] if kind else ['Политики', 'Продажи', 'Директора']
    for row in rows:
        branch = '' if global_scope else canonical(row['subdivision'], names)
        if not included(row['subdivision'], names):
            continue
        source_count += 1
        on_date = row['date'].date() if hasattr(row['date'], 'hour') else row['date']
        selected = next((item for item in reversed(timeline) if item.effective_from <= on_date), None)
        for employee_kind in kinds:
            employees = sorted(staff[key(branch)]) if employee_kind == 'Директора' and not global_scope else [0]
            for employee in employees:
                identity = (selected.pk if selected else None, key(branch), key(row['planning']), key(row['group']), key(row['brand']), key(employee_kind), employee)
                examined.add(identity)
                exact = index.get(identity)
                missing = sorted(set(f'K{i}' for i in range(5)) - (exact['segments'] if exact else set()))
                formulas = sorted(exact['formulas']) if exact else []
                default = selected.fallback_variation if selected else ''
                actual = ', '.join(formulas) if formulas else default or legacy_formula(row['planning'])
                reasons = []
                if not selected:
                    reasons.append('Нет действующей версии')
                if missing:
                    reasons.append('Нет точного коэффициента' if not exact else 'Не заполнены ступени: ' + ', '.join(missing))
                if len(formulas) > 1:
                    reasons.append('Разные формулы у ступеней')
                if default and formulas and any(value != default for value in formulas):
                    reasons.append('Явная формула отличается от формулы версии')
                if not reasons:
                    continue
                group_key = (*identity, tuple(reasons), actual)
                if group_key not in grouped:
                    grouped[group_key] = {'subdivision': branch or 'Глобально', 'planning': row['planning'], 'group': row['group'], 'brand': row['brand'],
                        'kind': employee_kind, 'employee': director_names.get(employee, ''), 'employee_id': employee,
                        'subdivision_id': branch_ids.get(key(branch)), 'version_id': selected.pk if selected else None, 'version': str(selected) if selected else 'Нет версии',
                        'reason': '; '.join(reasons), 'missing': ', '.join(missing), 'formula': actual,
                        'uses_fallback': not bool(formulas), 'amount': Decimal(0), 'documents': 0}
                grouped[group_key]['amount'] += number(row.get('amount'))
                grouped[group_key]['documents'] += int(row.get('documents') or 0)
    issues = sorted(grouped.values(), key=lambda row: (row['subdivision'], row['kind'], row['planning'], row['group'], row['brand'], row['employee']))
    return {'source_rows': source_count, 'examined': len(examined), 'missing': sum(bool(row['missing']) for row in issues),
            'formula_mismatches': sum('Явная формула' in row['reason'] for row in issues), 'issues': issues,
            'scope': scope, 'fallback_formula': version.fallback_variation or 'Прежнее правило по группе товара'}


def plan_warning(version, plan):
    result = check(version, plan_rows(plan))
    return {'examined': result['examined'], 'missing': result['missing'], 'formula_mismatches': result['formula_mismatches'],
            'examples': [{**row, 'amount': str(row['amount'])} for row in result['issues'][:20]], 'source': 'Загруженный план продаж'}
