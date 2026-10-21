"""Read-only comparison of global and branch budgets over identical plan lines."""
from decimal import Decimal
from django.core.exceptions import ValidationError
from ..models import DirectorEmployee, Subdivision, SubdivisionDirectorCoeff
from .comparison import Index, coefficient_rows
from .director_comparison import DirectorIndex
from .markup import motivation_markup
from .plan_summary_groups import ensure_summary_groups, summary_versions

ZERO = Decimal(0)
KINDS = [('policies', 'Политика'), ('sales', 'Продажи'), ('directors', 'Директор')]


def selections(params, field):
    values = params.getlist(field) if hasattr(params, 'getlist') else params.get(field, [])
    if isinstance(values, str):
        values = [values]
    return list(dict.fromkeys(str(value).strip() for value in values if str(value).strip()))


def summary_filters(scenario, params, allowed_subdivisions=None):
    lines = ensure_summary_groups(scenario)
    if allowed_subdivisions is not None:
        lines = lines.filter(subdivision__in=allowed_subdivisions)
    # Obtain both menus with one narrow scan of the plan, instead of two DISTINCT sorts.
    scopes = list(lines.order_by().values_list('subdivision', 'planning_group_sales').distinct())
    branch_keys = {sub for sub, pg in scopes}
    names = dict(Subdivision.objects.filter(subdivision_key__in=branch_keys).values_list('subdivision_key', 'subdivision_name'))
    staff = DirectorEmployee.objects.filter(pk__in=SubdivisionDirectorCoeff.objects.filter(
        subdivision__subdivision_key__in=branch_keys).values('employee_id')).order_by('full_name', 'pk')
    options = {
        'subdivision': [(key, names.get(key, key)) for key in sorted(branch_keys)],
        'type': KINDS,
        'planning_group_sales': [(key, key or 'Без группы') for key in {pg for sub, pg in scopes}],
        'director': [(str(employee.pk), employee.full_name) for employee in staff],
    }
    captions = {'subdivision': 'Подразделение', 'type': 'Тип коэффициента',
                'planning_group_sales': 'Группа планов продаж', 'director': 'Директор'}
    return [{'name': field, 'label': captions[field], 'options': [
        {'value': value, 'label': label, 'selected': value in selections(params, field)}
        for value, label in sorted(choices, key=lambda item: item[1])]} for field, choices in options.items()]


def summary_report(scenario, params, allowed_subdivisions=None):
    selected_types = selections(params, 'type')
    if set(selected_types) - {key for key, label in KINDS}:
        raise ValidationError('Неизвестный тип коэффициента.')
    kinds = [key for key, label in KINDS if not selected_types or key in selected_types]
    selected_staff = selections(params, 'director')
    if any(not value.isdecimal() for value in selected_staff):
        raise ValidationError('Некорректный директор.')
    selected_staff = {int(value) for value in selected_staff}
    versions = summary_versions()
    groups = ensure_summary_groups(scenario, versions)
    ids = [v.pk for v in versions]
    indexes = {key: Index(coefficient_rows(ids, subdivision=local))
               for key, local in [('global', False), ('local', True)]}
    directors = {key: DirectorIndex(ids, local=local)
                 for key, local in [('global', False), ('local', True)]}
    names = dict(DirectorEmployee.objects.values_list('pk', 'full_name'))
    branches = dict(Subdivision.objects.values_list('subdivision_key', 'subdivision_name'))
    lines = groups
    if allowed_subdivisions is not None:
        lines = lines.filter(subdivision__in=allowed_subdivisions)
    if selected_staff:
        allowed = {sub for (version, sub), staff in directors['local'].employees.items() if staff & selected_staff}
        lines = lines.filter(subdivision__in=allowed)
    for field in ['subdivision', 'planning_group_sales', 'group', 'brand']:
        selected = selections(params, field)
        if selected:
            lines = lines.filter(**{field + '__in': selected})
    buckets, missing = {}, set()
    policy_kinds = [(key, source) for key, source in [('policies', 'Политики'), ('sales', 'Продажи')] if key in kinds]
    for version, sub, pg, group, brand, values in lines.order_by().values_list(
            'version_key', 'subdivision', 'planning_group_sales', 'group', 'brand', 'amounts').iterator(chunk_size=2000):
        plan = Decimal(values['plan'])
        ddp = Decimal(values['ddp']) if values['ddp'] is not None else None
        amounts = [Decimal(value) for value in values['segments']]
        staff_values = directors['local'].by_employee(version, sub, pg, group, brand, amounts) if 'directors' in kinds or selected_staff else {}
        if selected_staff:
            staff_values = {staff: value for staff, value in staff_values.items() if staff in selected_staff}
            if not staff_values:
                continue
        bucket = buckets.setdefault(sub, empty_bucket())
        bucket['plan'] += plan
        bucket['ddp'] = add(bucket['ddp'], ddp)
        global_director = directors['global'].motivation(version, '', pg, group, brand, amounts) if 'directors' in kinds else ZERO
        if policy_kinds:
            for side in ['global', 'local']:
                try:
                    matrix, _ = indexes[side].coefficients(version, sub if side == 'local' else '', pg, group, brand,
                                                          kinds=[source for key, source in policy_kinds])
                    values = [sum((k * usd for k, usd in zip(row, amounts)), ZERO) for row in matrix]
                except ValidationError:
                    values = [None] * len(policy_kinds)
                    missing.add(side)
                for (kind, source), value in zip(policy_kinds, values):
                    bucket[side][kind] = add(bucket[side][kind], value)
        if 'directors' in kinds:
            bucket['global']['directors'] += global_director * len(staff_values)
            bucket['local']['directors'] += sum(staff_values.values(), ZERO)
            for staff, value in staff_values.items():
                employee = bucket['staff'].setdefault(staff, {'plan': ZERO, 'ddp': ZERO, 'global': ZERO, 'local': ZERO})
                employee['plan'] += plan
                employee['ddp'] = add(employee['ddp'], ddp)
                employee['global'] += global_director
                employee['local'] += value
    company = empty_bucket()
    for bucket in buckets.values():
        company['plan'] += bucket['plan']
        company['ddp'] = add(company['ddp'], bucket['ddp'])
        for side in ['global', 'local']:
            for kind in ['policies', 'sales', 'directors']:
                company[side][kind] = add(company[side][kind], bucket[side][kind])
        for staff, value in bucket['staff'].items():
            employee = company['staff'].setdefault(staff, {'plan': ZERO, 'ddp': ZERO, 'global': ZERO, 'local': ZERO})
            for key in employee:
                employee[key] = add(employee[key], value[key])
    nodes = [node('Итого', company, names, kinds)] if buckets else []
    if nodes:
        nodes[0]['is_total'] = True
    nodes += [node(branches.get(key, key), bucket, names, kinds) for key, bucket in sorted(buckets.items())]
    return {'summary_nodes': nodes, 'summary_missing': bool(missing),
            'summary_missing_ddp': any(bucket['ddp'] is None for bucket in buckets.values()),
            'summary_preliminary': not any(v.status == 'approved' for v in versions)}


def add(left, right):
    return None if left is None or right is None else left + right


def empty_bucket():
    return {'plan': ZERO, 'ddp': ZERO, 'staff': {}, **{side: dict.fromkeys(['policies', 'sales', 'directors'], ZERO) for side in ['global', 'local']}}


def row(name, plan, global_value, local_value, children=None, ddp=None):
    global_markup = motivation_markup(plan, ddp, global_value)
    local_markup = motivation_markup(plan, ddp, local_value)
    return {'name': name, 'plan': plan, 'ddp': ddp,
            'global_markup': global_markup, 'local_markup': local_markup,
            'markup_difference': local_markup - global_markup if global_markup is not None and local_markup is not None else None,
            'global': global_value, 'local': local_value,
            'difference': local_value - global_value if global_value is not None and local_value is not None else None,
            'global_percent': global_value / plan * 100 if plan and global_value is not None else None,
            'local_percent': local_value / plan * 100 if plan and local_value is not None else None,
            'children': children or []}


def node(name, bucket, names, kinds):
    plan = bucket['plan']
    children = [row(label, plan, bucket['global'][kind], bucket['local'][kind], ddp=bucket['ddp'])
                for kind, label in KINDS if kind in kinds]
    for child in children:
        if child['name'] == 'Директор':
            child['children'] = [row(names.get(staff, str(staff)), value['plan'], value['global'], value['local'], ddp=value['ddp'])
                for staff, value in sorted(bucket['staff'].items(), key=lambda item: (names.get(item[0], ''), item[0]))]
    total = {side: None if any(value is None for value in bucket[side].values()) else sum(bucket[side].values(), ZERO)
             for side in ['global', 'local']}
    return row(name, plan, total['global'], total['local'], children, ddp=bucket['ddp'])
