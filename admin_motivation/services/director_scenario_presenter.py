"""Read-only display of pre-gate quarterly premiums, including old snapshots."""
from collections import Counter,defaultdict
from decimal import Decimal


def with_quarter_premiums(data):
    if not data:return data
    data=dict(data);data['rows']=[dict(row) for row in data.get('rows',[])]
    key=lambda row:tuple(row.get(field) for field in ('scenario','employee','rate_source','branch'))
    counts=Counter(key(row) for row in data['rows'])
    totals=defaultdict(lambda:Decimal(0));seen=set();invalid=set()
    for detail in data.get('details',[]):
        period=detail.get('period')
        if period not in ('1 квартал','2 квартал','3 квартал','4 квартал'):continue
        item=(key(detail),int(period[0]));seen.add(item)
        if detail.get('possible') is None:invalid.add(item)
        else:totals[item]+=Decimal(str(detail['possible']))
    for row in data['rows']:
        for q in range(1,5):
            field=f'q{q}_possible';item=(key(row),q)
            if field not in row:
                row[field]=(totals[item] if counts[key(row)]==1 and item in seen and item not in invalid
                    and (row['year']==2025 or q<=3) else None)
    return data
