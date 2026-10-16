"""Read-only comparison of global and branch budgets over identical plan lines."""
from decimal import Decimal
from django.core.exceptions import ValidationError
from ..models import GlobalCoeffVersion, DirectorEmployee, Subdivision
from .comparison import Index, coefficient_rows
from .director_comparison import DirectorIndex
from .sales_plans import SEGMENTS, number

ZERO = Decimal(0)


def summary_report(scenario, params):
    versions = GlobalCoeffVersion.objects.exclude(status='superseded')
    if versions.filter(status='approved').exists():
        versions = versions.filter(status='approved')
    versions = list(versions.order_by('effective_from', 'pk'))
    ids = [v.pk for v in versions]
    indexes = {key: Index(coefficient_rows(ids, subdivision=local))
               for key, local in [('global', False), ('local', True)]}
    directors = {key: DirectorIndex(ids, local=local)
                 for key, local in [('global', False), ('local', True)]}
    names = dict(DirectorEmployee.objects.values_list('pk', 'full_name'))
    branches = dict(Subdivision.objects.values_list('subdivision_key', 'subdivision_name'))
    lines = scenario.lines.exclude(subdivision='')
    for field in ['subdivision', 'planning_group_sales', 'group', 'brand']:
        if params.get(field, '').strip():
            lines = lines.filter(**{field: params[field].strip()})
    buckets, dates, missing = {}, {}, set()
    for line in lines.order_by('pk').iterator(chunk_size=2000):
        bucket = buckets.setdefault(line.subdivision, empty_bucket())
        bucket['plan'] += line.amount_usd
        if line.plan_date not in dates:
            dates[line.plan_date] = next((v.pk for v in reversed(versions) if v.effective_from <= line.plan_date), None)
        version = dates[line.plan_date]
        amounts = [number(line.segment_amounts.get(segment)) for segment in SEGMENTS]
        staff_values = directors['local'].by_employee(version, line.subdivision, line.planning_group_sales, line.group, line.brand, amounts)
        global_director = directors['global'].motivation(version, '', line.planning_group_sales, line.group, line.brand, amounts)
        for side in ['global', 'local']:
            sub = line.subdivision if side == 'local' else ''
            try:
                matrix, _ = indexes[side].coefficients(version, sub, line.planning_group_sales, line.group, line.brand)
                values = [sum((k * usd for k, usd in zip(row, amounts)), ZERO) for row in matrix]
            except ValidationError:
                values = [None, None]
                missing.add(side)
            for kind, value in zip(['policies', 'sales'], values):
                bucket[side][kind] = add(bucket[side][kind], value)
            bucket[side]['directors'] += (global_director * len(staff_values) if side == 'global' else sum(staff_values.values(), ZERO))
        for staff, value in staff_values.items():
            employee = bucket['staff'].setdefault(staff, {'plan': ZERO, 'global': ZERO, 'local': ZERO})
            employee['plan'] += line.amount_usd
            employee['global'] += global_director
            employee['local'] += value
    company = empty_bucket()
    for bucket in buckets.values():
        company['plan'] += bucket['plan']
        for side in ['global', 'local']:
            for kind in ['policies', 'sales', 'directors']:
                company[side][kind] = add(company[side][kind], bucket[side][kind])
        for staff, value in bucket['staff'].items():
            employee = company['staff'].setdefault(staff, {'plan': ZERO, 'global': ZERO, 'local': ZERO})
            for key in employee:
                employee[key] += value[key]
    nodes = [node('Итого', company, names)] if buckets else []
    nodes += [node(branches.get(key, key), bucket, names) for key, bucket in sorted(buckets.items())]
    return {'summary_nodes': nodes, 'summary_missing': bool(missing),
            'summary_preliminary': not any(v.status == 'approved' for v in versions)}


def add(left, right):
    return None if left is None or right is None else left + right


def empty_bucket():
    return {'plan': ZERO, 'staff': {}, **{side: dict.fromkeys(['policies', 'sales', 'directors'], ZERO) for side in ['global', 'local']}}


def row(name, plan, global_value, local_value, children=None):
    return {'name': name, 'plan': plan, 'global': global_value, 'local': local_value,
            'global_percent': global_value / plan * 100 if plan and global_value is not None else None,
            'local_percent': local_value / plan * 100 if plan and local_value is not None else None,
            'children': children or []}


def node(name, bucket, names):
    plan = bucket['plan']
    children = [row(label, plan, bucket['global'][kind], bucket['local'][kind])
                for kind, label in [('policies', 'Политика'), ('sales', 'Продажи'), ('directors', 'Директор')]]
    children[2]['children'] = [row(names.get(staff, str(staff)), value['plan'], value['global'], value['local'])
                               for staff, value in sorted(bucket['staff'].items(), key=lambda item: (names.get(item[0], ''), item[0]))]
    total = {side: None if any(value is None for value in bucket[side].values()) else sum(bucket[side].values(), ZERO)
             for side in ['global', 'local']}
    return row(name, plan, total['global'], total['local'], children)
