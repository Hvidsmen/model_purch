"""Period totals computed in SQL; build a four-level expandable report."""
from urllib.parse import urlencode
from django.db.models import Count, Sum
from .markup import motivation_markup

DIMENSIONS = ['subdivision', 'planning_group_sales', 'group', 'brand']
AMOUNTS = ['plan', 'ddp', 'policies', 'sales', 'directors', 'total']


def period_report(scenario, scope, params):
    from .subdivision_scope import excluded_plan_names, included
    from .plan_scope import global_lines_without_excluded
    excluded = excluded_plan_names(scenario) if scope == 'global' else set()
    base = scenario.lines.exclude(subdivision='') if scope == 'subdivisions' else scenario.lines.filter(subdivision='')
    virtual = global_lines_without_excluded(scenario) if scope == 'global' and excluded else None
    filters = {field: params.get(field, '').strip() for field in DIMENSIONS}
    if scope == 'global':
        filters['subdivision'] = ''
    # Aggregate once: derive filter choices and apply dimensional filters to period rows.
    all_rows = list(base.values(*DIMENSIONS).annotate(plan=Sum('amount_usd'), ddp=Sum('amount_ddp_usd'), policies=Sum('policies_usd'),
        sales=Sum('sales_usd'), directors=Sum('directors_usd'), directors_count=Count('directors_usd'), total=Sum('total_usd'), row_count=Count('pk'), ddp_count=Count('amount_ddp_usd'), calculated_count=Count('total_usd')).order_by(*DIMENSIONS))
    if virtual is not None:
        all_rows = snapshot_rows(virtual)
    from .subdivision_aliases import mapping, canonical, expand
    names = mapping()
    if scope == 'subdivisions':
        excluded = {row['subdivision'] for row in all_rows if not included(row['subdivision'], names)}
        all_rows = [row for row in all_rows if row['subdivision'] not in excluded]
        base = base.exclude(subdivision__in=excluded)
    for row in all_rows:
        row['subdivision'] = canonical(row['subdivision'], names)
    choices = {field: sorted({row[field] for row in all_rows}) for field in DIMENSIONS}
    rows = [row for row in all_rows if all(not value or row[field] == value for field, value in filters.items())]
    for row in rows:
        if row['ddp_count'] != row['row_count']:
            row['ddp'] = None
        if row['directors_count'] != row['row_count']:
            row['directors'] = None
        row['markup'] = motivation_markup(row['plan'], row['ddp'], row['total']) if row['calculated_count'] == row['row_count'] else None
    lines = virtual if virtual is not None else base
    for field, value in filters.items():
        if value:
            if virtual is not None:
                lines = [line for line in lines if getattr(line, field) == value]
            else:
                lines = lines.filter(subdivision__in=expand(value, names)) if field == 'subdivision' else lines.filter(**{field: value})
    root = {'children': {}, **empty_totals()}
    for row in rows:
        current = root
        add_totals(current, row)
        for level, field in enumerate(DIMENSIONS):
            label = row[field] or 'Глобальный план'
            current = current['children'].setdefault(label, {'name': label, 'level': level, 'children': {}, **empty_totals()})
            add_totals(current, row)
    def materialize(node):
        if node['ddp_count'] != node['row_count']:
            node['ddp'] = None
        if node['directors_count'] != node['row_count']:
            node['directors'] = None
        node['markup'] = motivation_markup(node['plan'], node['ddp'], node['total']) if node['calculated_count'] == node['row_count'] else None
        node['children'] = [materialize(child) for child in node['children'].values()]
        return node
    materialize(root)
    query = {'scope': scope, **{field: value for field, value in filters.items() if value}}
    links = {}
    for mode in ['global', 'subdivisions']:
        data = {**query, 'scope': mode}
        if mode == 'global':
            data.pop('subdivision', None)
        links[mode] = '?' + urlencode(data)
    return {'report_nodes': root['children'], 'report_rows': rows, 'totals': {key: root[key] for key in [*AMOUNTS, 'markup']},
            'directors_pending': sum(row['row_count'] - row['directors_count'] for row in rows),
            'pending_count': root['row_count'] - root['calculated_count'], 'filters': filters, 'filter_choices': choices,
            'export_url': '?' + urlencode({**query, 'export': 'csv'}), 'scope_links': links, 'report_lines': lines}


def empty_totals():
    return {**{key: None for key in AMOUNTS}, 'row_count': 0, 'calculated_count': 0, 'ddp_count': 0, 'directors_count': 0}


def add_totals(node, row):
    for key in AMOUNTS:
        if row[key] is not None:
            node[key] = (node[key] or 0) + row[key]
    for key in ['row_count', 'calculated_count', 'ddp_count', 'directors_count']:
        node[key] += row[key]


def snapshot_rows(lines):
    fields = dict(zip(AMOUNTS, ['amount_usd','amount_ddp_usd','policies_usd','sales_usd','directors_usd','total_usd']))
    grouped = {}
    for line in lines:
        key = tuple(getattr(line, field) for field in DIMENSIONS)
        row = grouped.setdefault(key, {**dict(zip(DIMENSIONS,key)), **empty_totals()})
        for label, field in fields.items():
            value = getattr(line, field)
            if value is not None:
                row[label] = (row[label] or 0) + value
        row['row_count'] += 1
        row['ddp_count'] += line.amount_ddp_usd is not None
        row['directors_count'] += line.directors_usd is not None
        row['calculated_count'] += line.total_usd is not None
    return [grouped[key] for key in sorted(grouped)]
