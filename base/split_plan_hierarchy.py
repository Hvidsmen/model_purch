"""Read-only hierarchy and subtotals over unique planning-group leaves."""
from hashlib import sha256
import json
from .split_engine import precise
from .split_matrix_totals import aggregate
from .split_stock_overview import complete_sum


LEVELS = [('sales_plan_group', 'Группа планов продаж'), ('brand', 'Марка'),
          ('outdoor_type', 'Тип наружного блока'), ('factory', 'Завод')]


def history_total(gids, historical, channels):
    if not historical.get('snapshot'):
        return {'years': []}
    sources = {row['gid']: row for row in historical['groups']}
    result = {'years': []}
    for index, year in enumerate(historical['years']):
        selected = [sources[gid]['years'][index] for gid in gids]
        annual = [aggregate(source['annual'][c] for source in selected) for c in range(len(channels))]
        monthly = []
        for month in range(12):
            cells = [aggregate(source['months'][month]['cells'][c] for source in selected) for c in range(len(channels))]
            monthly.append({'month': f'{year}-{month+1:02}', 'cells': cells,
                            'by_channel': dict(zip(channels, cells)), 'total': aggregate(cells)})
        result['years'].append({'year': year, 'annual': annual, 'by_channel': dict(zip(channels, annual)),
                                'total': aggregate(annual), 'months': monthly})
    return result


@precise
def presentation(scenario, details, evaluated, historical, inventory):
    root = {'children': {}, 'leaves': []}
    channels = list(scenario.state['channels'])
    lines = {(line['gid'], line['cid']): {**line, 'qty': line['quantity']} for line in evaluated['lines']}
    for gid, group in scenario.state['groups'].items():
        parent, path = root, []
        reference = details[gid]['reference']
        for level, (field, label) in enumerate(LEVELS):
            value = str(reference.get(field) or '').strip() or 'Не указано'
            # A mixed classification stays one composite value, never duplicates a group.
            path.append(value)
            identity = sha256(json.dumps(path, ensure_ascii=False).encode()).hexdigest()[:20]
            node = parent['children'].setdefault(value, {'id': 'subtotal-' + identity, 'parent': parent.get('id', ''),
                'level': level, 'indent': level * 16, 'label': label, 'name': value, 'gids': [], 'children': {}, 'leaves': []})
            node['gids'].append(gid)
            parent = node
        parent['leaves'].append({'kind': 'group', 'gid': gid, 'group': group, 'parent': parent['id']})
    rows = []
    def emit(node):
        gids = node['gids']
        node['count'] = len(gids)
        node['channels'] = {cid: aggregate(lines.get((gid, cid), {'qty': None, 'cost': None, 'revenue': None}) for gid in gids) for cid in channels}
        node['total'] = aggregate(lines.get((gid, cid), {'qty': None, 'cost': None, 'revenue': None}) for gid in gids for cid in channels)
        node['history'] = history_total(gids, historical, channels)
        node['inventory'] = {field: complete_sum(inventory['groups'][gid][field] for gid in gids)
                             for field in ('stock', 'prior', 'forecast', 'opening', 'existing', 'new', 'closing')}
        rows.append({'kind': 'subtotal', 'node': node})
        for child in sorted(node['children'].values(), key=lambda value: value['name'].casefold()):
            emit(child)
        rows.extend(sorted(node['leaves'], key=lambda value: value['group']['ref']['name'].casefold()))
    for node in sorted(root['children'].values(), key=lambda value: value['name'].casefold()):
        emit(node)
    return rows
