"""Period totals computed in SQL; build a four-level expandable report."""
from urllib.parse import urlencode
from django.db.models import Count, Sum
from .markup import motivation_markup

DIMENSIONS = ['subdivision', 'planning_group_sales', 'group', 'brand']
AMOUNTS = ['plan', 'ddp', 'policies', 'sales', 'total']


def period_report(scenario, scope, params):
    base = scenario.lines.exclude(subdivision='') if scope == 'subdivisions' else scenario.lines.filter(subdivision='')
    filters = {field: params.get(field, '').strip() for field in DIMENSIONS}
    if scope == 'global':
        filters['subdivision'] = ''
    # Aggregate once: derive filter choices and apply dimensional filters to period rows.
    all_rows = list(base.values(*DIMENSIONS).annotate(plan=Sum('amount_usd'), ddp=Sum('amount_ddp_usd'), policies=Sum('policies_usd'),
        sales=Sum('sales_usd'), total=Sum('total_usd'), row_count=Count('pk'), ddp_count=Count('amount_ddp_usd'), calculated_count=Count('total_usd')).order_by(*DIMENSIONS))
    choices = {field: sorted({row[field] for row in all_rows}) for field in DIMENSIONS}
    rows = [row for row in all_rows if all(not value or row[field] == value for field, value in filters.items())]
    for row in rows:
        if row['ddp_count'] != row['row_count']:
            row['ddp'] = None
        row['markup'] = motivation_markup(row['plan'], row['ddp'], row['total']) if row['calculated_count'] == row['row_count'] else None
    lines = base
    for field, value in filters.items():
        if value:
            lines = lines.filter(**{field: value})
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
            'pending_count': root['row_count'] - root['calculated_count'], 'filters': filters, 'filter_choices': choices,
            'export_url': '?' + urlencode({**query, 'export': 'csv'}), 'scope_links': links, 'report_lines': lines}


def empty_totals():
    return {**{key: None for key in AMOUNTS}, 'row_count': 0, 'calculated_count': 0, 'ddp_count': 0}


def add_totals(node, row):
    for key in AMOUNTS:
        if row[key] is not None:
            node[key] = (node[key] or 0) + row[key]
    for key in ['row_count', 'calculated_count', 'ddp_count']:
        node[key] += row[key]
