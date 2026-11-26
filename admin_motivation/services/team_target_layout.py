"""Group goal cards by department/year/period so columns share row heights."""


def coefficient_cards(rows):
    """One card per kind/branch/year/period; overlapping bases remain conditions."""
    groups={}
    for row in rows:
        key=tuple(str(row.get(field) or '').strip().casefold()
                  for field in ('subdivision','year','kind','title'))
        if key not in groups:
            groups[key]={**row,'targets':[],'display_targets':[],'_periods':{}}
        group=groups[key]
        for target in row.get('targets',[]):
            group['targets'].append(target)
        # Use unmerged periods: merging IV/year in one formula must not hide
        # a distinct annual condition in another formula.
        for target in row.get('targets',row.get('display_targets',[])):
            group['_periods'].setdefault(target['label'],[]).append({
                **target,'formula':row.get('formula'),'scope_author':row.get('scope_author')})
    result=[]
    for row in groups.values():
        periods=row.pop('_periods')
        for label,conditions in periods.items():
            if len(conditions)==1:
                target={**conditions[0]}
            else:
                target={'label':label,'conditions':conditions,'plan':None,'fact':None,
                        'ratio':None,'remaining':None,'progress':0}
            ratio=target.get('ratio')
            target.setdefault('progress',min(max(ratio*100,0),100) if ratio is not None else 0)
            row['display_targets'].append(target)
        row['display_targets'].sort(key=lambda item: '5' if item['label']=='Год' else item['label'])
        result.append(row)
    return result


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
