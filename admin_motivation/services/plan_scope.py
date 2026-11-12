"""Project legacy global plan snapshots onto participating branches without DB writes."""
from copy import copy, deepcopy
from decimal import Decimal
from .subdivision_scope import excluded_plan_names


def global_lines_without_excluded(scenario, *, input_only=False, excluded=None, summary_only=False):
    excluded = excluded_plan_names(scenario) if excluded is None else excluded
    if not excluded:
        return None
    from .sales_plans import SEGMENTS, KINDS, number,policy_segments
    from .subdivision_aliases import mapping, canonical
    from ..models import SalesPlanLine
    names = mapping()
    grouped = {}
    source = scenario.lines.exclude(subdivision='').exclude(subdivision__in=excluded)
    for day, sub, pg, group, brand, amount, ddp, segments in source.order_by().values_list(
            'plan_date', 'subdivision', 'planning_group_sales', 'group', 'brand', 'amount_usd', 'amount_ddp_usd', 'segment_amounts').iterator(chunk_size=2000):
        key = (day, pg, group, brand)
        data = grouped.setdefault(key, {'plan': Decimal(0), 'ddp': Decimal(0), 'usd': [Decimal(0)] * 5, 'policy': [Decimal(0)] * 5, 'branches': {}})
        data['plan'] += amount
        data['ddp'] = data['ddp'] + ddp if data['ddp'] is not None and ddp is not None else None
        branch = data['branches'].setdefault(canonical(sub, names), [Decimal(0)] * 5) if not input_only else None
        for i,value in enumerate(policy_segments(segments,pg)):data['policy'][i]+=value
        for index, segment in enumerate(SEGMENTS):
            value = number(segments.get(segment))
            data['usd'][index] += value
            if branch is not None:
                branch[index] += value
    original_lines=scenario.lines.filter(subdivision='')
    if summary_only:
        original_lines=original_lines.only('scenario_id','plan_date','planning_group_sales','group','brand','calculation','total_usd').order_by()
    else:
        original_lines=original_lines.select_related('coefficient_version')
    def entries():
        if summary_only and not input_only:
            # Consume one bounded batch of audit JSON at a time; do not retain
            # every original snapshot merely to display aggregated amounts.
            for original in original_lines.iterator(chunk_size=128):
                key=(original.plan_date,original.planning_group_sales,original.group,original.brand)
                data=grouped.pop(key,None)
                if data is not None:yield key,data,original
            for key,data in grouped.items():yield key,data,None
        else:
            originals={(line.plan_date,line.planning_group_sales,line.group,line.brand):line
                       for line in (original_lines.iterator(chunk_size=2000) if not input_only else ())}
            for key,data in grouped.items():yield key,data,originals.get(key)
    result = []
    for (day, pg, group, brand), data, original in entries():
        line = copy(original) if original else SalesPlanLine(scenario=scenario, subdivision='', plan_date=day,
                                                           planning_group_sales=pg, group=group, brand=brand)
        line.subdivision=''
        line.amount_usd, line.amount_ddp_usd = data['plan'], data['ddp']
        line.segment_amounts = dict(zip(SEGMENTS, map(str, data['usd'])))
        line.segment_amounts.update({f'P{i}':str(value) for i,value in enumerate(data['policy'])})
        line.calculation = deepcopy(original.calculation) if original and not summary_only else {}
        records=original.calculation if original and summary_only else line.calculation
        line.policies_usd = line.sales_usd = line.directors_usd = line.total_usd = None
        if original and original.total_usd is not None:
            totals = []
            try:
                for kind in KINDS:
                    total = Decimal(0)
                    for segment, amount in zip(SEGMENTS, data['policy'] if kind=='Политики' else data['usd']):
                        record = records[kind][segment]
                        value = number(record['coefficient']) * amount
                        if not summary_only:record.update(usd=str(amount), motivation=str(value))
                        total += value
                    totals.append(total)
                employees = [];directors=Decimal(0)
                for record in records['Директора']['employees']:
                    branches = {canonical(sub, names) for sub in record['subdivisions'] if sub not in excluded}
                    branches &= data['branches'].keys()
                    if not branches:
                        continue
                    amounts = [sum((data['branches'][sub][i] for sub in branches), Decimal(0)) for i in range(5)]
                    if len(record['coefficients']) != 5:
                        raise KeyError('Incomplete director rates')
                    value = sum((number(rate) * amount for rate, amount in zip(record['coefficients'], amounts)), Decimal(0))
                    directors+=number(value)
                    if not summary_only:
                        record.update(subdivisions=sorted(branches), segment_amounts=list(map(str, amounts)), motivation=str(value))
                        employees.append(record)
                if not summary_only:line.calculation['Директора'] = {'employees': employees, 'motivation': str(directors)}
                line.policies_usd, line.sales_usd = totals
                line.directors_usd, line.total_usd = directors, sum(totals) + directors
            except (KeyError, TypeError):
                # Old snapshots without rates cannot be reconstructed reliably.
                line.calculation = {}
        result.append(line)
    return result
