"""Project the same indicative forecast onto the detailed product hierarchy."""
from decimal import Decimal
from .premium_forecast import total
from .plan_premium_forecast import empty_plan_known
from .reports_next import period_code
from .manager_detail import DIMENSIONS

ZERO=Decimal(0)


def enrich(detail,ledger):
    prepared=[]
    identities={(r.get('Подразделение'),r.get('АвторСделки'),r.get('ТипКоэф')) for r in detail.get('_source_rows',[])}
    def detail_branch(row):
        branch=row.get('Подразделение');kind=row['kind'];author=row.get('АвторСделки')
        for candidate in (branch,str(kind)+' · '+str(branch)):
            if (candidate,author,kind) in identities:return candidate
        return None
    for original in ledger.get('plan_forecast_rows',[]):
        row=dict(original);branch=detail_branch(row)
        if branch is None:continue
        row['Подразделение']=branch
        row['_forecast']=(row['quarter'],row['annual']);row['_code']=row['code'];prepared.append(row)
    represented={(*(r.get(k) for k in DIMENSIONS),r.get('kind'),r['_code']) for r in prepared}
    for original in detail.get('_source_rows',[]):
        code=period_code(original.get('КварталМотивации',''))
        key=(*(original.get(k) for k in DIMENSIONS),original.get('ТипКоэф'),code)
        if key not in represented:
            prepared.append({**original,'_code':code,'_forecast':(ZERO,ZERO) if empty_plan_known(ledger,original.get('ТипКоэф')) else (None,None)})
            represented.add(key)
    def values(rows):
        rows=list(rows)
        blocks=[]
        for label in detail['periods']:
            subset=[r for r in rows if r['_code']==period_code(label)]
            blocks.append([total(r['_forecast'][i] for r in subset) for i in (0,1)])
        quarters=[r for r in rows if r['_code']!='5'];years=[r for r in rows if r['_code']=='5']
        blocks.append([total(r['_forecast'][0] for r in quarters) if quarters else ZERO,total(r['_forecast'][1] for r in (years or quarters))])
        return blocks
    def append(blocks,rows):
        for block,forecast in zip(blocks,values(rows)):block.extend(forecast)
    def walk(nodes,rows):
        for node in nodes:
            subset=[r for r in rows if (r.get(DIMENSIONS[node['level']]) or '—')==node['label']]
            append(node['values'],subset)
            walk(node.get('children',[]),subset)
            for group in node.get('formula_groups',[]):
                scoped=[r for r in subset if (str(r.get('VariationCalculate') or '').strip() or 'Без способа расчёта')==group['label']]
                append(group['values'],scoped);walk(group.get('children',[]),scoped)
    append(detail['detail_totals'],prepared);walk(detail['nodes'],prepared)
    for block in detail.get('division_execution',[]):block.extend([None,None])
    detail['headers'].extend([dict(label='Прогноз при 100% · квартальная часть',detail=False,title='План × доли K0–K4 за 2025 год × ставки'),dict(label='Прогноз при 100% · в годовую премию',detail=False,title='Оценка годового начисления до годового условия выплаты')])
    detail['_forecast_audit']=[r for r in ledger.get('plan_forecast_rows',[]) if detail_branch(r) is not None]
    detail['extended_forecast']=True
