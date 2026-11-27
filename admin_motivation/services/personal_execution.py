"""Independent department and coefficient-kind execution above personal products."""
from collections import defaultdict
from decimal import Decimal
from .reports_next import period_code
from .reports import number,ratio

ZERO=Decimal(0)
NAMES={'Политики':'политиков','Продажи':'продаж','Директора':'директоров','Кураторы':'кураторов'}


def rows(ledger,data,kind,branch):
    periods=data['periods']
    visible_years={str(r.get('Год')) for r in data.get('_source_rows',[]) if r.get('Подразделение')==branch}
    department=[r for r in ledger.get('department_plan_fact',[]) if r.get('subdivision')==branch and str(r.get('year')) in visible_years]
    kind_rows=[r for r in data.get('coefficient_performance',[]) if r.get('subdivision')==branch and str(r.get('year')) in visible_years]
    if 'coefficient_performance' not in data:
        # Ordinary reports retain their native calculation windows. Restrict to
        # this branch before aggregating; another branch's execution cannot leak.
        from .manager_detail import metrics
        source=[r for r in data.get('_performance_rows') or [] if r.get('Подразделение')==branch and str(r.get('Год')) in visible_years]
        for label in periods:
            selected=[r for r in source if period_code(r.get('КварталМотивации'))==period_code(label)]
            if selected:
                block=metrics(selected,'USD',coefficients=False)
                kind_rows.append({'period':label,'year':'native','plan':block[14],'fact':block[15]})
    threshold=ledger.get('payout_threshold')
    if threshold is None:threshold=Decimal('.8')
    def metric(source,code):
        by_year=defaultdict(set)
        for r in source:
            if period_code(r.get('period'))==code:
                by_year[str(r.get('year'))].add((number(r['plan']) if r.get('plan') is not None else None,number(r['fact']) if r.get('fact') is not None else None))
        values=[next(iter(v)) for v in by_year.values() if len(v)==1]
        known=bool(by_year) and len(values)==len(by_year) and all(p is not None and f is not None for p,f in values)
        plan=sum((p for p,f in values),ZERO) if known else None
        fact=sum((f for p,f in values),ZERO) if known else None
        execution=ratio(fact,plan) if known else None
        return {'possible':None,'remaining':max(plan-fact,ZERO) if plan is not None and plan>0 else None,'ratio':execution,
                'calculated':None,'forecast_quarter':None,'forecast_annual':None,
                'status':'nr-matrix-good' if execution is not None and execution>=threshold else 'nr-matrix-low' if execution is not None else ''}
    result=[]
    for label,source in [('% выполнения подразделения',department),('% выполнения '+NAMES.get(kind,kind.casefold()),kind_rows)]:
        values=[metric(source,period_code(p)) for p in periods]
        basis=max(range(len(periods)),key=lambda i:period_code(periods[i])) if periods else None
        result.append({'label':label,'metrics':values,'total_metrics':dict(values[basis]) if basis is not None else {},'bonus':None,'total':None})
    return result
