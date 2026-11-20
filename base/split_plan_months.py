"""Read-only monthly sales cells, using the same exact allocations as the engine."""
from django.core.exceptions import ValidationError
from .split_engine import allocate,number,quantity,planning_months,precise,ZERO,HUNDRED
from .split_matrix_totals import aggregate


def cell(qty=None,cost=None,revenue=None):
    return {'qty':qty,'cost':cost,'revenue':revenue,'markup':revenue/cost if revenue is not None and cost else None}


def spread(value,weights):
    if value is None:return [None]*len(weights)
    if not sum(weights,ZERO):return [ZERO if value==ZERO else None]*len(weights)
    return allocate(value,weights)


@precise
def report(scenario,page_ids,hierarchy):
    months=planning_months(scenario);channels=list(scenario.state['channels']);groups={}
    for gid,group in scenario.state['groups'].items():
        bands={cid:{m:cell() for m in months} for cid in channels}
        powers={key:{cid:{m:cell() for m in months} for cid in channels} for key in group['powers']}
        for cid,line in group['channels'].items():
            try:
                qty=quantity(scenario,line);by_qty=line.get('allocation')=='quantity'
                weights=[number(line.get('month_quantities',{}).get(m) if by_qty else line['months'].get(m),optional=True) for m in months]
                if any(v is None for v in weights) or sum(weights,ZERO)!=(qty if by_qty else HUNDRED):continue
                quantities=spread(qty,weights)
                ddp=number(group.get('ddp'),optional=True);markup=number(line.get('markup'),optional=True)
                cost=qty*ddp if ddp is not None else None
                costs=spread(cost,quantities)
                revenues=spread(cost*markup if cost is not None and markup is not None else None,quantities)
                for m,q,c,r in zip(months,quantities,costs,revenues):bands[cid][m]=cell(q,c,r)
                keys=[k for k,p in group['powers'].items() if p.get('active')]
                shares=[number(group['powers'][k].get('share'),optional=True) for k in keys]
                if any(v is None for v in shares) or sum(shares,ZERO)!=HUNDRED:continue
                for m in months:
                    q=spread(bands[cid][m]['qty'],shares)
                    factors=[number(group['powers'][k].get('factor'),optional=True) for k in keys]
                    valid=all(v is not None and v>0 for v in factors)
                    weights=[v*f for v,f in zip(q,factors)] if valid else []
                    cost_parts=spread(bands[cid][m]['cost'],weights) if valid and bands[cid][m]['cost'] is not None else [None]*len(keys)
                    revenue_parts=spread(bands[cid][m]['revenue'],weights) if valid and bands[cid][m]['revenue'] is not None else [None]*len(keys)
                    for key,a,b,c in zip(keys,q,cost_parts,revenue_parts):powers[key][cid][m]=cell(a,b,c)
            except ValidationError:continue
        bands['all']={m:aggregate(bands[cid][m] for cid in channels) for m in months}
        for power in powers.values():power['all']={m:aggregate(power[cid][m] for cid in channels) for m in months}
        groups[gid]={'bands':bands,'powers':powers}
    def total(ids):return {band:{m:aggregate(groups[gid]['bands'][band][m] for gid in ids) for m in months} for band in ['all',*channels]}
    return {'months':months,'groups':groups,'page':total(page_ids),'all':total(list(groups)),
            'nodes':{entry['node']['id']:total(entry['node']['gids']) for entry in hierarchy if entry['kind']=='subtotal'}}
