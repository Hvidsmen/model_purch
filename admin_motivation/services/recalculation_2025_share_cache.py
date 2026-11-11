"""Choose an author-share scope once per classified policy-plan key."""
import re
from django.core.exceptions import ValidationError
from .pilot_formula import mask_sql
from .recalculation_2025_share_fallback import SHARE_JOIN,SCOPE_MATCH

MARKER='-- Portal 2025 materialized policy-plan share choices.'
FIELDS={'s.SubdivisionName':'PortalSubdivisionName','s.SubdivisionKey':'PortalSubdivisionKey',
 's.Chanel':'PortalChannel',**{'sc.'+key:'Portal'+key for key in
 ('PGSales','Group_','Brand','TypeCoeff','VariationCalculate','K0','K1','K2','K3','K4')}}

# Keep comparison/null semantics of the original fallback predicates.
MATCH=SCOPE_MATCH.format(alias='pick').replace('s.SubdivisionKey','k.PortalSubdivisionKey').replace(
 'sc.PGSales','k.PortalPGSales').replace('sc.Group_','k.PortalGroup_').replace('sc.Brand','k.PortalBrand')
CACHE="""
SELECT DISTINCT PortalScopeId,PortalSubdivisionKey,PortalPGSales,PortalGroup_,PortalBrand
INTO #test2025_plan_scope_keys FROM #test2025_policy_plan_rows;
CREATE UNIQUE CLUSTERED INDEX IX_test2025_scope_keys ON #test2025_plan_scope_keys(PortalScopeId);
SELECT DISTINCT ПодразделениеДокумент,AllocationLevel,ГруппаПлановПродаж,ГруппаТоваров,Марка
INTO #test2025_available_scopes FROM #percent_plan_menger WHERE Percent_ IS NOT NULL;
SELECT k.PortalScopeId,MIN(pick.AllocationLevel) AS AllocationLevel
INTO #test2025_selected_scopes FROM #test2025_plan_scope_keys k
LEFT JOIN #test2025_available_scopes pick ON """+MATCH+"""
GROUP BY k.PortalScopeId;
CREATE UNIQUE CLUSTERED INDEX IX_test2025_selected_scopes ON #test2025_selected_scopes(PortalScopeId);
SELECT k.PortalScopeId,pm.АвторСделки,pm.Percent_
INTO #test2025_resolved_shares FROM #test2025_plan_scope_keys k
JOIN #test2025_selected_scopes selected_share ON selected_share.PortalScopeId=k.PortalScopeId
LEFT JOIN #percent_plan_menger pm ON pm.AllocationLevel=selected_share.AllocationLevel
 AND """+MATCH.replace('pick.','pm.')+""";
CREATE CLUSTERED INDEX IX_test2025_resolved_shares ON #test2025_resolved_shares(PortalScopeId);
"""


def patch(source):
    if MARKER in source:return source
    code=mask_sql(source)
    into=re.search(r'\bINTO\s+#plan\b',code,re.I)
    if not into:raise ValidationError('Не найдена таблица плана 2025 для кеширования долей.')
    from .recalculation_2025_sql import ALLOCATION
    if source.count(ALLOCATION)!=1:raise ValidationError('Не найдена граница долей плана 2025.')
    boundary=source.index(ALLOCATION)+len(ALLOCATION)
    select=re.search(r'\bSELECT\b',code[boundary:into.start()],re.I)
    if not select:raise ValidationError('Не найдена проекция плана 2025.')
    start=boundary+select.start()
    union=re.search(r'\bUNION\s+ALL\b',code[into.end():],re.I)
    if not union:raise ValidationError('Не найдена граница плана политиков 2025.')
    end=into.end()+union.start()
    branch=source[start:end]
    head=source[start:into.start()]
    tail=source[into.end():end]
    if tail.count(SHARE_JOIN)!=1 or re.search(r'\bpm\.',mask_sql(head),re.I) is None:
        raise ValidationError('Не распознано распределение плана 2025 для кеширования долей.')
    base=tail.replace(SHARE_JOIN,'',1)
    if re.search(r'\b(?:pm|selected_share)\.',mask_sql(base),re.I):
        raise ValidationError('Доли авторов используются до материализации плана 2025.')
    projection=','.join(key+' AS '+value for key,value in FIELDS.items())
    prelude=MARKER+'\nSELECT t.*,'+projection+""",
 DENSE_RANK() OVER(ORDER BY s.SubdivisionKey,sc.PGSales,sc.Group_,sc.Brand) AS PortalScopeId
INTO #test2025_policy_plan_rows
"""+base+';\n'+CACHE
    for key,value in FIELDS.items():
        alias,column=key.split('.')
        head=re.sub(r'\b'+alias+r'\.\[?'+re.escape(column)+r'\]?(?!\w)','t.'+value,head,flags=re.I)
    if re.search(r'\b(?:s|sc)\.',mask_sql(head),re.I):
        raise ValidationError('Не распознана проекция плана 2025 для кеширования долей.')
    result=head+"""INTO #plan
FROM #test2025_policy_plan_rows t
LEFT JOIN #test2025_resolved_shares pm ON pm.PortalScopeId=t.PortalScopeId
"""
    return source[:start]+prelude+result+source[end:]
