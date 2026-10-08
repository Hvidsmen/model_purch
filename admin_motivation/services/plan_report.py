"""Period totals computed in SQL; build a four-level expandable report."""
from urllib.parse import urlencode
from django.db.models import Count, Sum

DIMENSIONS = ['subdivision', 'planning_group_sales', 'group', 'brand']
AMOUNTS = ['plan', 'policies', 'sales', 'total']


def period_report(scenario, scope, params):
    base = scenario.lines.exclude(subdivision='') if scope == 'subdivisions' else scenario.lines.filter(subdivision='')
    filters = {field: params.get(field, '').strip() for field in DIMENSIONS}
    if scope == 'global':
        filters['subdivision'] = ''
    choices = {field: list(base.order_by(field).values_list(field, flat=True).distinct()) for field in DIMENSIONS}
    lines = base
    for field, value in filters.items():
        if value:
            lines = lines.filter(**{field: value})
    rows = list(lines.values(*DIMENSIONS).annotate(plan=Sum('amount_usd'), policies=Sum('policies_usd'),
        sales=Sum('sales_usd'), total=Sum('total_usd'), row_count=Count('pk'), calculated_count=Count('total_usd')).order_by(*DIMENSIONS))
    root = {'children': {}, **empty_totals()}
    for row in rows:
        current = root
        add_totals(current, row)
        for level, field in enumerate(DIMENSIONS):
            label = row[field] or 'Глобальный план'
            current = current['children'].setdefault(label, {'name': label, 'level': level, 'children': {}, **empty_totals()})
            add_totals(current, row)
    def materialize(node):
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
    return {'report_nodes': root['children'], 'report_rows': rows, 'totals': {key: root[key] for key in AMOUNTS},
            'pending_count': root['row_count'] - root['calculated_count'], 'filters': filters, 'filter_choices': choices,
            'export_url': '?' + urlencode({**query, 'export': 'csv'}), 'scope_links': links, 'report_lines': lines}


def empty_totals():
    return {**{key: None for key in AMOUNTS}, 'row_count': 0, 'calculated_count': 0}


def add_totals(node, row):
    for key in AMOUNTS:
        if row[key] is not None:
            node[key] = (node[key] or 0) + row[key]
    for key in ['row_count', 'calculated_count']:
        node[key] += row[key]
