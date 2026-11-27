"""Additive coefficient targets, independent of overlapping formula windows."""
from decimal import Decimal

from .reports import DIV_INPUT_DIMENSIONS, number, ratio
from .reports_next import period_code
from .shareholder_personal_targets import COVERAGE


def performance(rows,kind,periods,*,original_rules=True):
    periods=list(dict.fromkeys(periods))
    coverages={code:COVERAGE[code] if original_rules else (('1','2') if code=='2' else ('4',) if code=='5' else (code,)) for code in periods}
    total_coverage=tuple(sorted({q for coverage in coverages.values() for q in coverage}))
    unique={};groups={}
    for row in rows:
        context=(row.get('Подразделение'),row.get('Год'))
        groups.setdefault(context,{})
        code=period_code(row.get('КварталМотивации'))
        if code not in ('1','2','3','4'):continue
        key=(*context,code,*(row.get(field) for field in DIV_INPUT_DIMENSIONS))
        item=unique.setdefault(key,{'context':context,'code':code,'plan':None,'fact':None})
        for name,field in [('plan','СуммаПланUSDКвартал'),('fact','СуммаОтгрузокUSDКвартал')]:
            if row.get(field) is not None:
                value=number(row[field])
                item[name]=max(item[name],value) if item[name] is not None else value
    for item in unique.values():
        groups[item['context']].setdefault(item['code'],[]).append(item)
    result=[]
    for (branch,year),quarters in groups.items():
        for code,coverage in [*coverages.items(),*([('total',total_coverage)] if total_coverage else [])]:
            inputs=[item for q in coverage for item in quarters.get(q,[])]
            values={}
            for name in ('plan','fact'):
                values[name]=(sum((item[name] for item in inputs),Decimal(0))
                              if all(q in quarters for q in coverage) and all(item[name] is not None for item in inputs) else None)
            result.append({'subdivision':branch,'year':year,'kind':kind,
                           'coefficient_total':True,
                           'formula':'Общая база вида мотивации','scope_author':'Всё подразделение',
                           'period':'Итого' if code=='total' else 'Год' if code=='5' else code+' квартал',**values,
                           'ratio':ratio(values['fact'],values['plan']) if all(v is not None for v in values.values()) else None})
    return result
