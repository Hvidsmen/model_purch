"""Upper-level plan/fact bases; never sum repeated department/window totals."""
from decimal import Decimal

ZERO=Decimal(0)
KINDS={'Политики':0,'Продажи':1,'Директора':2}


def amount(value):return Decimal(str(value)) if value is not None else None


def summarize_plan_facts(details,year):
    grouped={};seen=set()
    for row in details:
        branch=row.get('branch');kind=row.get('kind')
        if not branch or kind not in KINDS:continue
        if year!=2025:
            day=str(row.get('date') or '')
            try:q=(int(day[5:7])-1)//3+1
            except (ValueError,TypeError):continue
            if q not in (1,2,3,4):continue
            period=f'{q} квартал';key=(branch,kind,period)
            # Multiple directors use the same base, once per original plan scope.
            scope=(branch,kind,row.get('original_branch'),day,row.get('planning'),row.get('goods'),row.get('brand'))
            if scope in seen:continue
            seen.add(scope)
            target=grouped.setdefault(key,{'branch':branch,'kind':kind,'period':period,'plan':ZERO,'fact':ZERO,'segments':[ZERO]*5,'note':'Факт равен плану: сценарий 100%'})
            p,f=amount(row.get('plan')),amount(row.get('fact'))
            target['plan']=target['plan']+p if target['plan'] is not None and p is not None else None
            target['fact']=target['fact']+f if target['fact'] is not None and f is not None else None
            segments=row.get('segments')
            if target['segments'] is not None and isinstance(segments,(list,tuple)) and len(segments)==5 and all(v is not None for v in segments):
                target['segments']=[a+amount(b) for a,b in zip(target['segments'],segments)]
            else:target['segments']=None
        else:
            period=str(row.get('period') or '');key=(branch,kind,period)
            p,f=row.get('department_plan'),row.get('department_fact')
            if p is None or f is None:
                if kind=='Директора' and row.get('formula')=='Подразделение(Глобально)':p,f=row.get('plan'),row.get('fact')
            target=grouped.setdefault(key,{'branch':branch,'kind':kind,'period':period,'plan':None,'fact':None,'segments':None,
                'note':'База периода не сохранена в этом запуске'})
            if p is None or f is None:continue
            p,f=amount(p),amount(f)
            if target.get('conflict'):continue
            if target['plan'] is not None and (target['plan']!=p or target['fact']!=f):
                target.update(plan=None,fact=None,conflict=True,note='В снимке различаются итоги базы периода; требуется проверка')
            else:target.update(plan=p,fact=f,note='Проверяемый период; накопительные периоды не складываются')
    result=[]
    for key in sorted(grouped,key=lambda k:(k[0],KINDS[k[1]],k[2])):
        row=grouped[key];row.pop('conflict',None)
        row['execution']=row['fact']/row['plan'] if row['plan'] is not None and row['fact'] is not None and row['plan']>0 else None
        result.append(row)
    return result
