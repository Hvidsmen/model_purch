"""Quarter disclosures and forecast for the shareholder detail summary."""
from collections import defaultdict
from decimal import Decimal
from .reports_next import period_code
from .premium_forecast import total

ZERO=Decimal(0)


def catchup_by_author(rows,currency):
    result={}
    for row in rows:
        if period_code(row.get('КварталМотивации',''))!='5':continue
        key=(row.get('Подразделение') or '—',row.get('АвторСделки') or '—')
        amount=Decimal(str(row.get('ОстатокRUR' if currency=='RUB' else 'Остаток') or 0))
        values=row.get('CatchupQuarters'+currency)
        if values is None and amount:
            result[key]=None;continue
        if key in result and result[key] is None:continue
        output=result.setdefault(key,{code:ZERO for code in '1234'})
        weights={code:Decimal(str(v)) for code,v in (values or {}).items()}
        denominator=sum(weights.values(),ZERO)
        if amount and not denominator:
            result[key]=None;continue
        for code,value in weights.items():
            if code in output and denominator:output[code]+=amount*value/denominator
    return result


def enrich(detail,ledger,display):
    summary=detail['period_summary']
    codes=list(dict.fromkeys(period_code(column['label']) for column in summary['columns']))
    quarter_codes=[code for code in codes if code in ('1','2','3','4')]
    prefixed=len(ledger.get('sections',[]))>1
    def branch(row):return (row['kind']+' · ' if prefixed else '')+row['subdivision']
    def attach(node,rows,forecasts):
        rows=list(rows);forecasts=list(forecasts)
        node['quarter_possible']=[dict(label='Год' if code=='5' else code+' кв.',value=total(r.get('possible') for r in rows if r['code']==code)) for code in codes if code in ('1','2','3','4','5')]
        node['quarter_annual']=[dict(label=code+' кв.',value=total(r.get('annual_accrual') for r in rows if r['code']==code)) for code in quarter_codes]
        node['annual_accrual']=total(r.get('annual_accrual') for r in rows if r['code'] in '1234')
        annual_rows=[r for r in rows if r['code']=='5']
        node['quarter_catchup']=[dict(label=code+' кв.',value=total((r['bonus_quarters'].get(code,ZERO) if r.get('bonus_quarters') is not None else None) for r in annual_rows) if annual_rows else ZERO) for code in '1234']
        node['forecast_100']={metric:total(r.get('forecast_100',{}).get(metric) for r in forecasts) for metric in ('quarter','annual')}
    forecasts=display.get('registry_rows',[])
    attach(summary['totals'],ledger['rows'],forecasts)
    for group in summary['groups']:
        group_rows=[r for r in ledger['rows'] if branch(r)==group['subdivision']]
        group_forecasts=[r for r in forecasts if branch(r)==group['subdivision']]
        attach(group,group_rows,group_forecasts)
        for author in group['authors']:
            attach(author,[r for r in group_rows if r['author']==author['author']],[r for r in group_forecasts if r['author']==author['author']])
    detail['quarter_breakdown']=True
