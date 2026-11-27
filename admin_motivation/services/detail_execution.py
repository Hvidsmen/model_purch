"""Independent execution bases in the extended shareholder matrix."""
from collections import defaultdict
from decimal import Decimal
from .reports import number,ratio
from .reports_next import period_code
from .personal_execution import NAMES


def enrich(detail,ledger):
    periods=detail['periods'];size=len(detail['headers'])
    contexts=set();sources=[]
    for section in ledger.get('sections',[]):
        data=section['data']
        visible={(r.get('Подразделение'),str(r.get('Год'))) for r in data.get('_source_rows',[])}
        if not visible:continue
        contexts.update(visible)
        source=[r for r in data.get('coefficient_performance',[]) if (r.get('subdivision'),str(r.get('year'))) in visible]
        sources.append(('% выполнения '+NAMES.get(section['kind'],section['kind'].casefold()),source,14,visible))
    department=[r for r in ledger.get('department_plan_fact',[]) if (r.get('subdivision'),str(r.get('year'))) in contexts]
    label='% выполнения подразделения' if len({b for b,y in contexts})<=1 else '% выполнения подразделений'
    result=[]
    for label,source,offset,expected in [(label,department,10,contexts),*sources]:
        blocks=[]
        for period in periods:
            code=period_code(period);by_context=defaultdict(set)
            for row in source:
                if period_code(row.get('period'))!=code:continue
                key=(row.get('subdivision'),str(row.get('year')))
                if ' · ' in period and key[1]!=period.split(' · ',1)[0]:continue
                by_context[key].add((number(row['plan']) if row.get('plan') is not None else None,
                                     number(row['fact']) if row.get('fact') is not None else None))
            pairs=[next(iter(v)) for v in by_context.values() if len(v)==1]
            required={key for key in expected if ' · ' not in period or key[1]==period.split(' · ',1)[0]}
            known=bool(required) and set(by_context)==required and len(pairs)==len(by_context) and all(p is not None and f is not None for p,f in pairs)
            block=[None]*size
            if known:
                plan=sum((p for p,f in pairs),Decimal(0));fact=sum((f for p,f in pairs),Decimal(0))
                block[offset:offset+3]=[plan,fact,ratio(fact,plan)]
            blocks.append(block)
        # Selected quarter windows may overlap; retain the latest target in total.
        latest=max(range(len(periods)),key=lambda i:(periods[i].split(' · ',1)[0] if ' · ' in periods[i] else '',period_code(periods[i]))) if periods else None
        blocks.append(list(blocks[latest]) if latest is not None else [None]*size)
        result.append({'label':label,'values':blocks})
    detail['execution_rows']=result
