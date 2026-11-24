"""Group goal cards by department/year/period so columns share row heights."""


def layout(presentation):
    columns=[]
    department=presentation.get('department_remaining_targets',[])
    if any(r.get('display_targets') for r in department):columns.append(('Всё подразделение',department,False))
    for section in presentation.get('team_sections',[]):
        rows=section.get('remaining_targets',[])
        if any(r.get('display_targets') for r in rows):columns.append((section['kind'],rows,True))
    groups={}
    for column,(label,rows,compact) in enumerate(columns):
        for row in rows:
            for target in row.get('display_targets',[]):
                period=str(target['label'])
                code='4' if period.startswith('IV') else '5' if period=='Год' else period[:1]
                key=(str(row.get('year') or ''),str(row.get('subdivision') or '').casefold(),code)
                cells=groups.setdefault(key,[{'label':name,'cards':[]} for name,_,_ in columns])
                cells[column]['cards'].append({'row':row,'target':target,'compact':compact})
    return [{'cells':groups[key]} for key in sorted(groups)]
