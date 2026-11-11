"""Conservatively narrow the private copy of the published prepared Sales."""
import re
from .pilot_formula import mask_sql


def columns_for(definition,cursor,*,historical_author=False):
    # Match identifiers anywhere in the remaining program, including
    # unqualified columns and helper arguments. Over-inclusion is harmless.
    # A star/dynamic consumer needs the original complete schema.
    if cursor is None:return '*',None
    code=mask_sql(definition)
    if re.search(r'\bEXEC(?:UTE)?\b|(?:\b\w+|\[[^\]]+\])\s*\.\s*\*',code,re.I):return '*',None
    if re.search(r'SELECT\s+(?:DISTINCT\s+)?(?:TOP\s*\(?\d+\)?\s+)?\*\s+(?:INTO\s+[#\w.\[\]]+\s+)?FROM\s+#portal_motivation_sales\b',code,re.I):return '*',None
    cursor.execute("SELECT name FROM sys.columns WHERE object_id=OBJECT_ID(N'dbo.Sales') ORDER BY column_id")
    rows=cursor.fetchall()
    if not isinstance(rows,(list,tuple)):return '*',None
    names=[row[0] for row in rows if not isinstance(row,str) and hasattr(row,'__len__') and len(row)==1 and isinstance(row[0],str)]
    if not {'closeddealdate','subdivision','productcode'}<={name.casefold() for name in names}:return '*',None
    tokens={(match[0][1:-1].replace(']]',']') if match[0].startswith('[') else match[0]).casefold()
        for match in re.finditer(r'\[(?:[^\]]|\]\])+\]|\w+',code)}
    selected=[name for name in names if name.casefold() in tokens]
    if historical_author:
        # This producer-only column is absent from dbo.Sales metadata.
        selected.append('PortalOriginalDealAuthor')
        if 'Даичи_АвторСделкиНаименование' not in selected:selected.append('Даичи_АвторСделкиНаименование')
    if not selected:return '*',None
    return ','.join('['+name.replace(']',']]')+']' for name in selected),len(selected)
