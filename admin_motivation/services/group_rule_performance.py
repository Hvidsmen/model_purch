"""Share one rule/minimum lookup per coefficient join and cache dated minima."""
import re
from django.core.exceptions import ValidationError
from .pilot_formula import mask_sql

CACHE='#share_group_minima'


def join_end(code,start,end):
    depth=0
    for token in re.finditer(r'\(|\)|\b(?:LEFT|RIGHT|INNER|FULL|CROSS|OUTER)\s+(?:OUTER\s+)?(?:JOIN|APPLY)\b|\b(?:JOIN|WHERE|GROUP\s+BY|ORDER\s+BY|HAVING)\b',code[start:end],re.I):
        value=token[0]
        if value=='(':depth+=1
        elif value==')':
            if depth==0:return start+token.start()
            depth-=1
        elif depth==0:return start+token.start()
    return end


def apply_position(code,start,end):
    alias=re.search(r'\bsc\s+ON\b',code[start:end],re.I)
    if not alias:raise ValidationError('Не найдено соединение ставок для оптимизации правил групп.')
    return join_end(code,start+alias.end(),end)


def materialize(source,table='#share_group_rules'):
    """Replace repeated minimum sources by exact interval aggregates, once/batch.

    Keep overlapping intervals: the row lookup takes MIN over all intervals
    active on its date, preserving independently selected K0-K4.
    """
    marker='-- __PORTAL_GROUP_MIN_SOURCE_BEGIN__'
    stop='-- __PORTAL_GROUP_MIN_SOURCE_END__'
    if marker not in source:return source
    first=source.index(marker)+len(marker)
    end=source.index(stop,first)
    relation=source[first:end].strip()
    source=re.sub(re.escape(marker)+r'[\s\S]*?'+re.escape(stop),CACHE,source)
    code=mask_sql(source)
    fact=re.search(r';\s*WITH\s+fact\s+AS\s*\(',code,re.I)
    if not fact:raise ValidationError('Не найдена граница подготовки минимумов групп.')
    cache=f'''
-- __PORTAL_GROUP_MINIMA_CACHE__
SELECT DateFrom,DateTo,Subdivision,PGSales,LTRIM(RTRIM(TypeCoeff)) AS TypeCoeff,
 MIN(K0) AS K0,MIN(K1) AS K1,MIN(K2) AS K2,MIN(K3) AS K3,MIN(K4) AS K4
INTO {CACHE} FROM {relation} grc
WHERE EXISTS(SELECT 1 FROM {table} r WHERE r.Missing=N'minimum'
 AND r.PGSales=grc.PGSales AND r.TypeCoeff=LTRIM(RTRIM(grc.TypeCoeff))
 AND r.Subdivision IN(N'',grc.Subdivision))
GROUP BY DateFrom,DateTo,Subdivision,PGSales,LTRIM(RTRIM(TypeCoeff));
CREATE INDEX IX_share_group_minima ON {CACHE}(Subdivision,PGSales,TypeCoeff,DateFrom,DateTo);
'''
    return source[:fact.start()]+cache+source[fact.start():]
