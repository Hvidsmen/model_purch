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
    employees, registry, periods, types = {}, {}, {}, {}
    result['total'] = ledger['total']
    def empty(row):
        return {'subdivision': row['subdivision'], 'author': row['author'], 'unallocated': row['unallocated'],
                'amount': ZERO, 'bonus': ZERO, 'total': ZERO, 'rows': [], 'kinds': set(),
                'period_amounts': defaultdict(lambda: ZERO), 'period_links': defaultdict(dict)}
    def add(group, row):
        for name in ('amount', 'bonus', 'total'):
            group[name] += row[name]
        group['rows'].append(row)
        group['kinds'].add(row['kind'])
        group['period_amounts'][row['code']] += row['amount']
        if row.get('detail_url'):
            group['period_links'][row['code']].setdefault(row['kind'], row['detail_url'])
    for row in ledger['rows']:
        result['base'] += row['amount']
        result['bonus'] += row['bonus']
        if row['unallocated']:
            result['unallocated'] += row['total']
        identity = (row['subdivision'], row['author'])
        add(employees.setdefault(identity, empty(row)), row)
        add(registry.setdefault((*identity, row['kind']), dict(empty(row), kind=row['kind'])), row)
        totals = types.setdefault(row['kind'], {'kind': row['kind'], 'amount': ZERO, 'bonus': ZERO, 'total': ZERO, 'people': set()})
        for name in ('amount', 'bonus', 'total'):
            totals[name] += row[name]
        if not row['unallocated']:
            totals['people'].add(identity)
        period_key = row['code']
        card = periods.setdefault(period_key, {'label': 'Год' if period_key == '5' else row['period'], 'amount': ZERO, 'bonus': ZERO, 'total': ZERO, 'links': {}})
        for name in ('amount', 'bonus', 'total'):
            card[name] += row[name]
        if row.get('detail_url'):
            card['links'].setdefault(row['kind'], row['detail_url'])
    def url_for_period(url, selected):
        if not url:
            return ''
        parts = urlsplit(url)
        params = QueryDict(parts.query, mutable=True)
        params.setlist('period', list(selected) if isinstance(selected, (list, tuple)) else [selected])
        return parts.path + '?' + params.urlencode()
    codes = sorted({row['code'] for row in ledger['rows'] if row['code']})
    result['period_columns'] = [{'code': code, 'label': 'Год' if code == '5' else f'{code} кв.'} for code in codes]
    for group in [*employees.values(), *registry.values()]:
        group['kinds'] = sorted(group['kinds'])
        group['detail_url'] = url_for_period(group['rows'][0].get('detail_url', ''), period)
        group['kind_links'] = [{'label': kind, 'url': url_for_period(next(row.get('detail_url', '') for row in group['rows'] if row['kind'] == kind), period)} for kind in group['kinds']]
        group['period_values'] = [group['period_amounts'][code] if code in group['period_amounts'] else None for code in codes]
        group['period_cells'] = [{'amount': group['period_amounts'][code] if code in group['period_amounts'] else None,
                                 'links': [{'label': kind, 'url': url} for kind, url in group['period_links'][code].items()]} for code in codes]
    result['employees'] = sorted(employees.values(), key=lambda row: (row['unallocated'], row['subdivision'], row['author']))
    result['registry_rows'] = sorted(registry.values(), key=lambda row: (row['unallocated'], row['subdivision'], row['author'], row['kind']))
    result['type_totals'] = [{**types[kind], 'people': len(types[kind]['people'])} if kind in types else
        {'kind': kind, 'amount': None, 'bonus': None, 'total': None, 'people': 0}
        for kind in dict.fromkeys([*ledger.get('kinds', []), *types])]
    result['people'] = sum(not row['unallocated'] for row in result['employees'])
    result['branches'] = len({row['subdivision'] for row in result['employees']})
    result['period_cards'] = [card for code, card in sorted(periods.items())]
    return result


def personal_details(ledger, query):
    """Expose existing goal bases and the product matrix, without recalculating pay."""
    from calendar import monthrange
    from datetime import date
    from django.urls import reverse
    from .reports_next import period_code, selected_periods
    goals, sections = [], []
    for section in ledger.get('sections', []):
        data, kind = section['data'], section['kind']
        for index, label in enumerate(data['periods']):
            block = data['detail_totals'][index]
            plan, fact, ratio = block[14:17]
            remaining = max(plan - fact, ZERO) if plan is not None and plan > 0 and fact is not None else None
            first_quarter = period_code(label) == '1'
            goals.append({'period': label, 'kind': kind, 'plan': plan, 'fact': fact, 'ratio': ratio,
                'remaining': remaining, 'first_quarter': first_quarter,
                'global_scope': data.get('global_subdivision_mode', False), 'threshold': ledger.get('payout_threshold'),
                'under_threshold': not first_quarter and ratio is not None and ledger.get('payout_threshold') is not None and ratio < ledger['payout_threshold'],
                'progress': min(max(ratio * 100, ZERO), Decimal(100)) if ratio is not None else ZERO,
                'possible': block[13], 'calculated': block[17], 'bonus': block[18], 'reward_at_target': None,
                'reward_reason': 'Выполнение считается по всему подразделению, а личная премия зависит от будущих личных отгрузок, товаров и ступеней скидки. Эти данные пока неизвестны.' if data.get('global_subdivision_mode') else 'В сохранённом расчёте нет отдельного прогноза премии при 100%. Для него нужно знать структуру будущих личных продаж и ставок.',
                'gap': max(block[13] - block[17], ZERO) if block[13] is not None else None})
        def matrix(node, branch, author, planning='', group=''):
            level = node['level']
            if level == 2:
                planning = node['label']
            if level == 3:
                group = node['label']
            target = query.copy()
            target['type'], target['run'] = kind, '1'
            target.setlist('subdivision', [branch])
            target.setlist('author', [author])
            for key, value in [('planning', planning), ('goods', group), ('brand', node['label'] if level == 4 else '')]:
                if value and value not in ('*', '—'):
                    target.setlist(key, [value])
                else:
                    target.pop(key, None)
            detail_url = reverse('motivation_report_next', args=['detail']) + '?' + target.urlencode() + '#next-deep-calculation'
            year = int(query['year'])
            codes = selected_periods(query)
            code = codes[0] if len(codes) == 1 else 'all'
            first_month, last_month = (int(code) * 3 - 2, int(code) * 3) if code in ('1', '2', '3', '4') else (1, 12)
            deals = QueryDict(mutable=True)
            for key, value in {'type': kind, 'subdivision': branch, 'author': author, 'run': '1',
                               'start': date(year, first_month, 1).isoformat(),
                               'end': date(year, last_month, monthrange(year, last_month)[1]).isoformat()}.items():
                deals[key] = value
            for key, value in [('planning', planning), ('goods', group), ('brand', node['label'] if level == 4 else '')]:
                if value and value not in ('*', '—'):
                    deals[key] = value
            deal_links = []
            for selected in (['5'] if '5' in codes else codes):
                start, end = (int(selected) * 3 - 2, int(selected) * 3) if selected != '5' else (1, 12)
                link = deals.copy()
                link['start'] = date(year, start, 1).isoformat()
                link['end'] = date(year, end, monthrange(year, end)[1]).isoformat()
                deal_links.append({'label': 'Год' if selected == '5' else selected + ' кв.', 'url': reverse('motivation_report', args=['deals']) + '?' + link.urlencode()})
            return {'label': node['label'], 'level': level,
                'values': [block[19] for block in node['values']],
                'detail_url': detail_url,
                'deals_url': deal_links[0]['url'], 'deal_links': deal_links,
                'children': [matrix(child, branch, author, planning, group) for child in node['children']]}
        groups = []
        for branch in data['nodes']:
            for author in branch['children']:
                if author.get('unallocated'):
                    continue
                groups.append({'branch': branch['label'], 'author': author['label'],
                    'children': [matrix(node, branch['label'], author['label']) for node in author['children']]})
        sections.append({'kind': kind, 'periods': data['periods'], 'groups': groups})
    return {'goals': goals, 'product_sections': sections}



def new_detail_labels(data):
    """Labels for the new tab only; legacy reports keep their terminology."""
    data = dict(data)
    data['headers'] = [dict(header) for header in data['headers']]
    for index, label, explanation in [
        (13, 'Возможная премия на текущих отгрузках', 'Премия по уже совершённым отгрузкам до применения условий выплаты; это не прогноз премии при 100% плана.'),
        (17, 'Премия по расчёту', 'Премия с учётом условий выполнения плана. Не подтверждает фактическую выплату.'),
        (18, 'Годовая доплата за кварталы', 'Годовая корректировка квартальных премий. Не сама годовая премия.'),
        (19, 'Всего по расчёту', 'Премия по расчёту плюс годовая доплата за кварталы.')]:
        data['headers'][index].update(label=label, title=explanation)
    return data
