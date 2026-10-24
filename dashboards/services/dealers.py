"""Dealer movements computed from signed monthly sales, without database writes."""
from collections import Counter, defaultdict
from datetime import date
from decimal import Decimal

THRESHOLD_GROUPS = ('S', 'M', 'L', 'XL')
ABC_GROUPS = ('C', 'B', 'A')
ABSENT = 'Нет продаж'


def periods(as_of, mode='ltm'):
    """LTM uses twelve complete months; labels always include exact boundaries."""
    result = [{'year': y, 'start': date(y, 1, 1), 'end': date(y + 1, 1, 1), 'label': str(y)}
              for y in range(2021, as_of.year)]
    if mode == 'ltm':
        end = as_of.replace(day=1)
        start = date(end.year - 1, end.month, 1)
        if start >= date(2021, 1, 1):
            result.append({'year': as_of.year, 'start': start, 'end': end, 'label': f'{as_of.year} · LTM'})
    return result


def segment(values, method, measure, thresholds=(10, 50, 250)):
    active = {key: item for key, item in values.items() if item['quantity'] > 0}
    if method == 'threshold':
        s_max, m_max, l_max = thresholds
        return {key: 'S' if item['quantity'] <= s_max else 'M' if item['quantity'] <= m_max else 'L' if item['quantity'] <= l_max else 'XL'
                for key, item in active.items()}
    weights = {key: max(item[measure], Decimal(0)) for key, item in active.items()}
    total = sum(weights.values())
    before = Decimal(0)
    groups = {}
    # Stable code ordering resolves ties identically across requests.
    for key in sorted(active, key=lambda key: (-weights[key], str(key))):
        share = before / total if total else Decimal(1)
        groups[key] = 'A' if share < Decimal('.80') else 'B' if share < Decimal('.95') else 'C'
        before += weights[key]
    return groups


def analyze(rows, options, as_of):
    method, scope, measure = options['method'], options['scope'], options['measure']
    thresholds = tuple(options.get(key, default) for key, default in [('s_max', 10), ('m_max', 50), ('l_max', 250)])
    groups = ABC_GROUPS if method == 'abc' else THRESHOLD_GROUPS
    rank = {name: index for index, name in enumerate(groups)}
    timeline = periods(as_of, options['period_mode'])
    valid_years = {period['year'] for period in timeline}
    if not timeline:
        return {'periods': [], 'empty': True}
    start_year, end_year = options.get('start'), options.get('end')
    start_year = start_year if start_year in valid_years else timeline[0]['year']
    end_year = end_year if end_year in valid_years else timeline[-1]['year']
    if start_year > end_year:
        start_year, end_year = end_year, start_year
    selected_branches = set(options.get('branch', []))
    brand_filter, goods_filter = set(options.get('brand', [])), set(options.get('equipment', []))
    filter_choices = {dimension: sorted({row[dimension] for row in rows}) for dimension in ('branch', 'brand', 'equipment')}
    rows = [row for row in rows if (not brand_filter or row['brand'] in brand_filter)
            and (not goods_filter or row['equipment'] in goods_filter)]
    totals = {period['year']: defaultdict(lambda: {'quantity': Decimal(0), 'amount': Decimal(0)}) for period in timeline}
    branch_totals = {period['year']: defaultdict(lambda: {'quantity': Decimal(0), 'amount': Decimal(0)}) for period in timeline}
    labels, label_dates, first_seen = {}, {}, {}
    month_periods = {}
    for row in rows:
        dealer, branch = row['dealer_code'], row['branch']
        key = (branch, dealer) if scope == 'branch' else ('', dealer)
        if key not in label_dates or (row['month'], row['dealer_name']) > (label_dates[key], labels[key]):
            labels[key] = row['dealer_name']
            label_dates[key] = row['month']
        if row['quantity'] > 0 and row['month'] >= date(2021, 1, 1):
            first_seen[key] = min(first_seen.get(key, row['month']), row['month'])
        if row['month'] not in month_periods:
            month_periods[row['month']] = [period for period in timeline if period['start'] <= row['month'] < period['end']]
        for period in month_periods[row['month']]:
            for collection, item_key in ((totals[period['year']], key), (branch_totals[period['year']], (branch, dealer))):
                item = collection[item_key]
                item['quantity'] += row['quantity']; item['amount'] += row['amount']
    membership = {}
    for year, values in totals.items():
        buckets = defaultdict(dict)
        for key, value in values.items():
            buckets[key[0]][key] = value
        membership[year] = {key: group for bucket in buckets.values() for key, group in segment(bucket, method, measure, thresholds).items()}
    visible = {}
    for period in timeline:
        year = period['year']
        if not selected_branches:
            visible[year] = dict(totals[year])
        else:
            values = defaultdict(lambda: {'quantity': Decimal(0), 'amount': Decimal(0)})
            for (branch, dealer), value in branch_totals[year].items():
                if branch in selected_branches:
                    key = (branch, dealer) if scope == 'branch' else ('', dealer)
                    values[key]['quantity'] += value['quantity']; values[key]['amount'] += value['amount']
            visible[year] = dict(values)
    def active(year):
        return {key for key, item in visible[year].items() if item['quantity'] > 0 and key in membership[year]}
    def group(year, key):
        return membership[year].get(key, ABSENT) if key in active_sets[year] else ABSENT
    active_sets = {year: active(year) for year in valid_years}
    def movement(year, key):
        period = next(p for p in timeline if p['year'] == year)
        earlier = [p['year'] for p in timeline if p['year'] < year]
        if key not in active_sets[year]:
            return 'Ушёл' if earlier and key in active_sets[max(earlier)] else 'Нет продаж'
        if not earlier:
            return 'Новый'  # Initial cohort is explicitly labelled as the beginning of observed history.
        if key in active_sets[max(earlier)]:
            return 'Продолжает'
        if first_seen.get(key, period['start']) < period['start'] or any(key in active_sets[y] for y in earlier):
            return 'Вернулся'
        return 'Новый'
    overview = []
    base_top = {key for key in active_sets.get(2023, set()) if group(2023, key) == groups[-1]}
    for period in timeline:
        year = period['year']; members = active_sets[year]
        overview.append({**period, 'dealers': len(members), 'quantity': sum((v['quantity'] for v in visible[year].values()), Decimal(0)),
                         'amount': sum((v['amount'] for v in visible[year].values()), Decimal(0)),
                         'top': sum(group(year, key) == groups[-1] for key in members),
                         'retention_2023': Decimal(sum(key in members and group(year, key) == groups[-1] for key in base_top)) / len(base_top) if base_top and year >= 2023 else None,
                         'groups': [{'group': name, 'count': sum(group(year, key) == name for key in members),
                                     'value': sum((visible[year][key][measure] for key in members if group(year, key) == name), Decimal(0))} for name in groups]})
    cohort = options.get('cohort', '')
    selected_timeline = [p for p in timeline if start_year <= p['year'] <= end_year]
    keys = set().union(*(set(visible[p['year']]) for p in selected_timeline))
    transitions = defaultdict(lambda: {'count': 0, 'quantity': Decimal(0), 'amount': Decimal(0)})
    bridge = defaultdict(lambda: {'value': Decimal(0), 'groups': defaultdict(Decimal), 'count': 0})
    trajectories = defaultdict(lambda: {'count': 0, 'values': defaultdict(Decimal)})
    origins = defaultdict(Counter)
    for key in keys:
        before = visible[start_year].get(key, {'quantity': Decimal(0), 'amount': Decimal(0)})
        after = visible[end_year].get(key, {'quantity': Decimal(0), 'amount': Decimal(0)})
        ga, gb = group(start_year, key), group(end_year, key)
        if ga == ABSENT and gb == ABSENT and not any(key in active_sets[p['year']] for p in selected_timeline):
            # Retain signed returns in the bridge, even for inactive dealers.
            if before[measure] == after[measure]:
                continue
        destination = gb if gb != ABSENT else 'Ушли'
        baseline_end = next(p for p in timeline if p['year'] == start_year)['end']
        returned = first_seen.get(key, baseline_end) < baseline_end
        source = ga if ga != ABSENT else 'Вернулись' if returned else 'Новые'
        if options.get('collapse_low') and method == 'threshold':
            source = 'S + M' if source in ('S', 'M') else source
            destination = 'S + M' if destination in ('S', 'M') else destination
        trace = cohort not in groups or ga == cohort
        if trace and (ga != ABSENT or gb != ABSENT):
            flow = transitions[source, destination]
            flow['count'] += 1; flow['quantity'] += after['quantity']; flow['amount'] += after['amount']
        if ga == ABSENT and gb != ABSENT:
            category = 'Вернулся' if returned else 'Новый'
        elif ga != ABSENT and gb == ABSENT:
            category = 'Ушедшие'
        elif ga == ABSENT and gb == ABSENT:
            category = 'Возвраты без активных продаж'
        elif rank[gb] > rank[ga]:
            category = 'Переходы вверх'
        elif rank[gb] < rank[ga]:
            category = 'Переходы вниз'
        else:
            category = 'Изменение внутри группы'
        delta = after[measure] - before[measure]
        if ga != ABSENT or gb != ABSENT or delta:
            bridge[category]['value'] += delta; bridge[category]['count'] += 1
            bridge[category]['groups'][gb if gb != ABSENT else ga] += delta
        if trace and any(key in active_sets[p['year']] for p in selected_timeline):
            path = tuple(group(p['year'], key) for p in selected_timeline)
            trajectory = trajectories[path]; trajectory['count'] += 1
            for p in selected_timeline:
                trajectory['values'][p['year']] += visible[p['year']].get(key, {}).get(measure, Decimal(0))
        if gb != ABSENT:
            origin = 'Новые' if source == 'Новые' else 'Вернулись' if source == 'Вернулись' else 'Были в группе' if ga == gb else 'Выросли' if ga != ABSENT and rank[gb] > rank[ga] else 'Перешли из более высокой группы'
            origins[gb][origin] += 1
    cohorts = []
    for name in groups:
        members = {key for key in active_sets[start_year] if group(start_year, key) == name}
        cohorts.append({'group': name, 'size': len(members), 'periods': [{'year': p['year'],
             'retained': Decimal(sum(key in active_sets[p['year']] and rank.get(group(p['year'], key), -1) >= rank[name] for key in members)) / len(members) if members else None,
             'quantity': sum((visible[p['year']].get(key, {}).get('quantity', Decimal(0)) for key in members), Decimal(0))} for p in selected_timeline]})
    arrivals = []
    for p in timeline:
        year = p['year']; members = {key for key in active_sets[year] if movement(year, key) == 'Новый'}
        arrivals.append({'year': year, 'size': len(members), 'alive': len(members & active_sets[end_year]),
                         'top': sum(group(end_year, key) == groups[-1] for key in members)})
    branches = []
    branch_values = {year: defaultdict(dict) for year in (start_year, end_year)}
    for year, buckets in branch_values.items():
        for (branch, dealer), value in branch_totals[year].items():
            buckets[branch][dealer] = value
    for branch in sorted(set(branch_values[end_year]) | set(branch_values[start_year])):
        if selected_branches and branch not in selected_branches:
            continue
        result = {'branch': branch, 'groups': [], 'total': Decimal(0), 'top5_share': None}
        positive = sorted([v[measure] for v in branch_values[end_year][branch].values() if v['quantity'] > 0 and v[measure] > 0], reverse=True)
        result['top5_share'] = sum(positive[:5]) / sum(positive) if sum(positive) else None
        for name in groups:
            values = []
            for year in (start_year, end_year):
                items = branch_values[year][branch]
                def identity(dealer):
                    return (branch, dealer) if scope == 'branch' else ('', dealer)
                amount = sum((max(v[measure], Decimal(0)) for dealer, v in items.items()
                              if v['quantity'] > 0 and membership[year].get(identity(dealer)) == name), Decimal(0))
                base = sum((max(v[measure], Decimal(0)) for dealer, v in items.items()
                            if v['quantity'] > 0 and identity(dealer) in membership[year]), Decimal(0))
                values.append(amount / base if base else Decimal(0))
            result['groups'].append({'group': name, 'share': values[1], 'delta': values[1] - values[0]})
        result['total'] = sum((v[measure] for v in branch_values[end_year][branch].values()), Decimal(0))
        branches.append(result)
    dealer_code = options.get('dealer', '')
    search = options.get('search', '').strip().casefold()
    visible_keys = set().union(*(set(values) for values in visible.values()))
    canonical_names = {}
    for key in sorted(visible_keys):
        candidate = (label_dates[key], labels[key])
        if candidate > canonical_names.get(key[1], (date.min, '')):
            canonical_names[key[1]] = candidate
    dealer_options = [{'code': code, 'name': label[1]} for code, label in sorted(canonical_names.items())
                      if not search or search in code.casefold() or search in label[1].casefold()]
    dealer = None
    if dealer_code in canonical_names:
        relevant_keys = [key for key in visible_keys if key[1] == dealer_code and (not selected_branches or scope != 'branch' or key[0] in selected_branches)]
        dealer = {'code': dealer_code, 'names': sorted({labels[key] for key in relevant_keys}), 'periods': [], 'equipment': [], 'brands': [], 'branches': [], 'locations': []}
        for p in timeline:
            dealer['periods'].append({'year': p['year'], 'label': p['label'], 'quantity': sum((visible[p['year']].get(key, {}).get('quantity', Decimal(0)) for key in relevant_keys), Decimal(0)),
                                     'amount': sum((visible[p['year']].get(key, {}).get('amount', Decimal(0)) for key in relevant_keys), Decimal(0)),
                                     'groups': [{'branch': key[0] or 'Компания', 'group': group(p['year'], key), 'status': movement(p['year'], key)} for key in relevant_keys]})
        for dimension, target in [('equipment', 'equipment'), ('brand', 'brands'), ('branch', 'branches')]:
            values = defaultdict(Decimal)
            for row in rows:
                if row['dealer_code'] == dealer_code and (not selected_branches or row['branch'] in selected_branches):
                    if next(p for p in timeline if p['year'] == end_year)['start'] <= row['month'] < next(p for p in timeline if p['year'] == end_year)['end']:
                        values[row[dimension]] += row[measure]
            dealer[target] = [{'label': name, 'value': value} for name, value in sorted(values.items())]
        dealer['locations'] = sorted({', '.join(filter(None, (row['region'], row['city']))) for row in rows if row['dealer_code'] == dealer_code})
    bridge_start = sum((visible[start_year].get(key, {}).get(measure, Decimal(0)) for key in keys), Decimal(0))
    bridge_end = sum((visible[end_year].get(key, {}).get(measure, Decimal(0)) for key in keys), Decimal(0))
    return {'thresholds': thresholds, 'cohort': cohort if cohort in groups else '', 'empty': not any(active_sets.values()), 'periods': timeline, 'groups': list(groups), 'overview': overview,
            'start': start_year, 'end': end_year, 'measure': measure, 'method': method, 'scope': scope,
            'flows': [{'source': a, 'target': b, **value} for (a, b), value in sorted(transitions.items())],
            'bridge': {'start': bridge_start, 'end': bridge_end, 'items': [{'label': name, **value, 'groups': dict(value['groups'])} for name, value in bridge.items()],
                       'difference': bridge_end - bridge_start, 'balanced': bridge_end - bridge_start == sum((item['value'] for item in bridge.values()), Decimal(0))},
            'trajectories': [{'path': list(path), 'count': item['count'], 'values': [item['values'][p['year']] for p in selected_timeline]} for path, item in sorted(trajectories.items(), key=lambda pair: (-pair[1]['count'], pair[0]))[:50]],
            'trajectory_periods': [p['year'] for p in selected_timeline], 'cohorts': cohorts,
            'origins': [{'group': name, 'origins': dict(origins[name])} for name in groups], 'arrivals': arrivals,
            'branches': branches, 'dealer': dealer, 'dealer_options': (dealer_options[:100] + [item for item in dealer_options[100:] if item['code'] == dealer_code]), 'dealer_matches': len(dealer_options),
            'filters': filter_choices,
            'rules': {'active': 'Активный дилер: чистое количество за период больше нуля в выбранном масштабе и в отображаемых филиалах. Возвраты сохраняют свой знак.',
                      'threshold': f'S: 0–{thresholds[0]} шт.; M: свыше {thresholds[0]}–{thresholds[1]}; L: свыше {thresholds[1]}–{thresholds[2]}; XL: свыше {thresholds[2]}. Пороги всегда в штуках.',
                      'abc': 'A: первые 80%, B: следующие 15%, C: последние 5% положительной выбранной меры. Пересекающий границу дилер остаётся в предыдущей группе.',
                      'history': 'Новые в 2021 году — начальная наблюдаемая база; более ранняя история неизвестна.',
                      'scope': 'При масштабе «Компания» группы считаются по всей компании, фильтр филиала меняет только отображаемые продажи. В масштабе «Филиал» один дилер может учитываться в нескольких филиалах.'}}
