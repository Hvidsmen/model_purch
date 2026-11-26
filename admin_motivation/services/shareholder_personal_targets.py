"""Personal targets from additive quarter inputs, before overlapping rule windows."""
from decimal import Decimal
from .reports import number, ratio
from .reports_next import period_code

COVERAGE={'1':('1',),'2':('1','2'),'3':('3',),'4':('1','2','3','4'),'5':('1','2','3','4')}


def targets(rows,periods):
    quarters={}
    for row in rows:
        code=period_code(row.get('КварталМотивации'))
        if code not in '1234' or not code:continue
        values=quarters.setdefault(code,[Decimal(0),Decimal(0),True])
        for index,field in enumerate(('СуммаПланUSDКвартал','СуммаОтгрузокUSDКвартал')):
            if row.get(field) is None:values[2]=False
            else:values[index]+=number(row[field])
    result={}
    for code in periods:
        coverage=COVERAGE[code]
        if not all(q in quarters and quarters[q][2] for q in coverage):continue
        plan=sum((quarters[q][0] for q in coverage),Decimal(0))
        fact=sum((quarters[q][1] for q in coverage),Decimal(0))
        result[code]={'plan':plan,'fact':fact,'ratio':ratio(fact,plan),'personal_plan':True}
    return result
