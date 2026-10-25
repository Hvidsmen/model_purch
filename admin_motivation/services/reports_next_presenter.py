"""Presentation only: grouping never changes calculated amounts or export rows."""
from collections import defaultdict
from decimal import Decimal
from urllib.parse import urlsplit
from django.http import QueryDict

ZERO = Decimal(0)


def present(kind, data, ledger=None, period='all'):
    result = {'people': 0, 'branches': 0, 'total': ZERO, 'base': ZERO, 'bonus': ZERO,
              'unallocated': ZERO, 'employees': [], 'period_cards': [], 'budget_rows': []}
    if kind == 'executive':
        if not data:
            return result
        root = data[0]
        result.update(global_plan=root['global'], local_plan=root['local'], difference=root['difference'],
                      difference_percent=root.get('difference_percent'), branches=max(0, len(data) - 1))
        scale = max([abs(row[side] or ZERO) for row in data[1:] for side in ('global', 'local')] + [Decimal(1)])
        for row in sorted(data[1:], key=lambda row: abs(row['difference'] or ZERO), reverse=True):
            result['budget_rows'].append({'node': row, 'global_width': abs(row['global'] or ZERO) / scale * 100,
                                          'local_width': abs(row['local'] or ZERO) / scale * 100})
        return result
    if ledger is None:
        return result
    employees, periods = {}, {}
    result['total'] = ledger['total']
    for row in ledger['rows']:
        result['base'] += row['amount']
        result['bonus'] += row['bonus']
        if row['unallocated']:
            result['unallocated'] += row['total']
        identity = (row['subdivision'], row['author'])
        employee = employees.setdefault(identity, {'subdivision': row['subdivision'], 'author': row['author'],
            'unallocated': row['unallocated'], 'amount': ZERO, 'bonus': ZERO, 'total': ZERO, 'rows': [], 'kinds': set()})
        for name in ('amount', 'bonus', 'total'):
            employee[name] += row[name]
        employee['rows'].append(row)
        employee['kinds'].add(row['kind'])
        period_key = row['code']
        card = periods.setdefault(period_key, {'label': 'Год' if period_key == '5' else row['period'], 'amount': ZERO, 'bonus': ZERO, 'total': ZERO, 'links': {}})
        for name in ('amount', 'bonus', 'total'):
            card[name] += row[name]
        if row.get('detail_url'):
            card['links'].setdefault(row['kind'], row['detail_url'])
    for employee in employees.values():
        employee['kinds'] = sorted(employee['kinds'])
        url = employee['rows'][0].get('detail_url', '')
        if url:
            parts = urlsplit(url)
            params = QueryDict(parts.query, mutable=True)
            params['period'] = period
            employee['detail_url'] = parts.path + '?' + params.urlencode()
        else:
            employee['detail_url'] = ''
    result['employees'] = sorted(employees.values(), key=lambda row: (row['unallocated'], row['subdivision'], row['author']))
    result['people'] = sum(not row['unallocated'] for row in result['employees'])
    result['branches'] = len({row['subdivision'] for row in result['employees']})
    result['period_cards'] = [card for code, card in sorted(periods.items())]
    return result
