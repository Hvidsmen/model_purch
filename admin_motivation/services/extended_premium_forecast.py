"""Project the same indicative forecast onto the detailed product hierarchy."""
from collections import defaultdict
from decimal import Decimal
from .premium_forecast import number,total
from .reports_next import period_code
from .manager_detail import DIMENSIONS

ZERO=Decimal(0)


def enrich(detail,ledger):
    bases=defaultdict(list)
    for row in ledger.get('coefficient_plan_fact',[]):
        code=period_code(row.get('period',''))
        if code in ('1','2','3','4','5'):bases[(row['subdivision'],row['kind'],code)].append(row)
    owners={(r['subdivision'],r['author'],r['kind'],r['code']):r for r in ledger['rows']}
    prepared=[];currency=ledger['currency'];field='СуммаМотивацииRUR' if currency=='RUB' else 'СуммаМотивацииUSD'
    prefixed=len(ledger.get('sections',[]))>1
    for original in detail.get('_source_rows',[]):
        row=dict(original);code=period_code(row.get('КварталМотивации',''))
        kind=row.get('ТипКоэф');branch=row.get('Подразделение') or '—'
        if prefixed and branch.startswith(str(kind)+' · '):branch=branch[len(str(kind))+3:]
        owner=owners.get((branch,row.get('АвторСделки') or '—',kind,code),{})
        base=bases.get((branch,kind,code),[]);factor=None
        if len(base)==1 and code!='1':
            plan,fact=number(base[0].get('plan')),number(base[0].get('fact'))
            if plan is not None and plan>0 and fact is not None and fact>0:factor=plan/fact
        possible=number(row.get(field));quarter=None;annual=None
        if factor is not None and possible is not None:
            quarter=possible*factor if code!='5' else ZERO
            if code=='5':annual=possible*factor
            else:
                owner_possible=number(owner.get('possible'));owner_annual=number(owner.get('annual_accrual'))
                if owner_possible is not None and owner_annual is not None:
                    if owner_possible:annual=possible*owner_annual/owner_possible*factor
                    elif owner_annual==0:annual=ZERO
        row['_forecast']=(quarter,annual);row['_code']=code;prepared.append(row)
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
    detail['headers'].extend([dict(label='Прогноз при 100% · квартальная часть',detail=False,title='Пропорциональная оценка при прежней структуре продаж, скидках и ставках'),dict(label='Прогноз при 100% · в годовую премию',detail=False,title='Оценка годового начисления до годового условия выплаты')])
    detail['extended_forecast']=True
