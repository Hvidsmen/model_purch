"""Project legacy global plan snapshots onto participating branches without DB writes."""
from copy import copy, deepcopy
from decimal import Decimal
from .subdivision_scope import excluded_plan_names


def global_lines_without_excluded(scenario, *, input_only=False):
    excluded = excluded_plan_names(scenario)
    if not excluded:
        return None
    from .sales_plans import SEGMENTS, KINDS, number
    from .subdivision_aliases import mapping, canonical
    from ..models import SalesPlanLine
    names = mapping()
    grouped = {}
    source = scenario.lines.exclude(subdivision='').exclude(subdivision__in=excluded)
    for day, sub, pg, group, brand, amount, ddp, segments in source.order_by().values_list(
            'plan_date', 'subdivision', 'planning_group_sales', 'group', 'brand', 'amount_usd', 'amount_ddp_usd', 'segment_amounts').iterator(chunk_size=2000):
        key = (day, pg, group, brand)
        data = grouped.setdefault(key, {'plan': Decimal(0), 'ddp': Decimal(0), 'usd': [Decimal(0)] * 5, 'branches': {}})
        data['plan'] += amount
        data['ddp'] = data['ddp'] + ddp if data['ddp'] is not None and ddp is not None else None
        branch = data['branches'].setdefault(canonical(sub, names), [Decimal(0)] * 5) if not input_only else None
        for index, segment in enumerate(SEGMENTS):
            value = number(segments.get(segment))
            data['usd'][index] += value
            if branch is not None:
                branch[index] += value
    originals = {(line.plan_date, line.planning_group_sales, line.group, line.brand): line
                 for line in (scenario.lines.filter(subdivision='').select_related('coefficient_version') if not input_only else ())}
    result = []
    for (day, pg, group, brand), data in grouped.items():
        original = originals.get((day, pg, group, brand))
        line = copy(original) if original else SalesPlanLine(scenario=scenario, subdivision='', plan_date=day,
                                                           planning_group_sales=pg, group=group, brand=brand)
        line.amount_usd, line.amount_ddp_usd = data['plan'], data['ddp']
        line.segment_amounts = dict(zip(SEGMENTS, map(str, data['usd'])))
        line.calculation = deepcopy(original.calculation) if original else {}
        line.policies_usd = line.sales_usd = line.directors_usd = line.total_usd = None
        if original and original.total_usd is not None:
            totals = []
            try:
                for kind in KINDS:
                    total = Decimal(0)
                    for segment, amount in zip(SEGMENTS, data['usd']):
                        record = line.calculation[kind][segment]
                        value = number(record['coefficient']) * amount
                        record.update(usd=str(amount), motivation=str(value))
                        total += value
                    totals.append(total)
                employees = []
                for record in line.calculation['Директора']['employees']:
                    branches = {canonical(sub, names) for sub in record['subdivisions'] if sub not in excluded}
                    branches &= data['branches'].keys()
                    if not branches:
                        continue
                    amounts = [sum((data['branches'][sub][i] for sub in branches), Decimal(0)) for i in range(5)]
                    if len(record['coefficients']) != 5:
                        raise KeyError('Incomplete director rates')
                    value = sum((number(rate) * amount for rate, amount in zip(record['coefficients'], amounts)), Decimal(0))
                    record.update(subdivisions=sorted(branches), segment_amounts=list(map(str, amounts)), motivation=str(value))
                    employees.append(record)
                directors = sum((number(record['motivation']) for record in employees), Decimal(0))
                line.calculation['Директора'] = {'employees': employees, 'motivation': str(directors)}
                line.policies_usd, line.sales_usd = totals
                line.directors_usd, line.total_usd = directors, sum(totals) + directors
            except (KeyError, TypeError):
                # Old snapshots without rates cannot be reconstructed reliably.
                line.calculation = {}
        result.append(line)
    return result
