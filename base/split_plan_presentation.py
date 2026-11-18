"""Reference columns and read-only power detail for the sales plan matrix."""
from django.core.exceptions import ValidationError

from .models import SalesPlanningGoods
from .split_engine import ZERO, precise, quantity
from .split_matrix_totals import aggregate


def reference_fields(name, goods):
    def values(field):
        return ', '.join(sorted({str(getattr(good, field, '')).strip() for good in goods
                                 if str(getattr(good, field, '')).strip()}))
    return {'sales_plan_group': values('sales_plan_group'), 'brand': values('brand'),
            'outdoor_type': values('outdoor_block_type'),
            'factory': values('manufacturer')}


@precise
def presentation(scenario, evaluated):
    groups = scenario.state['groups']
    keys = {model['key'] for group in groups.values() for power in group['powers'].values()
            for model in power['models']}
    goods = {good.source_key: good for good in SalesPlanningGoods.objects.filter(source_key__in=keys)}
    shipments = {}
    for row in evaluated['shipments']:
        shipments.setdefault((row['group_key'], row['power'], row['channel_id']), []).append(row)
    result = {}
    for gid, group in groups.items():
        models = {model['key'] for power in group['powers'].values() for model in power['models']}
        reference = group['ref'].get('details') or reference_fields(group['ref']['name'], [goods[key] for key in models if key in goods])
        if not reference.get('factory'):
            reference = {**reference, 'factory': reference_fields(group['ref']['name'], [goods[key] for key in models if key in goods])['factory']}
        if not reference.get('brand'):
            reference = {**reference, 'brand': ', '.join(group['ref'].get('brand', []))}
        powers = []
        for key, power in group['powers'].items():
            channels = {}
            for cid, line in group['channels'].items():
                rows = shipments.get((group['ref']['source_key'], key, cid), [])
                try:
                    zero = quantity(scenario, line) == ZERO
                except ValidationError:
                    zero = False
                if rows:
                    qty = sum((row['quantity'] for row in rows if row['kind'] == 'indoor'), ZERO)
                    total_cost = sum((row['cost'] for row in rows), ZERO)
                    total_revenue = sum((row['revenue'] for row in rows), ZERO)
                    cell = {'qty': qty, 'cost': total_cost, 'revenue': total_revenue,
                            'markup': total_revenue / total_cost if total_cost else None}
                else:
                    cell = {field: ZERO if zero else None for field in ('qty', 'cost', 'revenue')}
                    cell['markup'] = None
                channels[cid] = cell
            total = aggregate(channels.values())
            powers.append({'key': key, 'active': power['active'], 'channels': channels, 'total': total,
                           'ddp': total['cost'] / total['qty'] if total['qty'] and total['cost'] is not None else None})
        result[gid] = {'reference': reference, 'powers': powers}
    return result
