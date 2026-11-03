"""Limit preparation to sales consumed by this year's calculation.

Document selection uses document/product keys, never document dates: an old
invoice or a correction can belong to a deal closed in the selected year.
Unknown shapes are left untouched rather than applying a broad SQL rewrite.
"""
import re
from .pilot_formula import mask_sql, closing

MARKER = '-- Portal preparation scoped to the selected fact year.'
SALES = r'(?:\[?DataWH\]?\s*\.\s*)?\[?dbo\]?\s*\.\s*\[?Sales\]?'


def patch(definition):
    mask = mask_sql(definition)
    if MARKER in definition or not re.search(r'@FactYear\b', mask, re.I):
        return definition
    # Only the original preparation block before the fact CTE is supported.
    fact = re.search(r';\s*WITH\s+fact\s+AS\s*\(', mask, re.I)
    updates = list(re.finditer(r'\bUPDATE\s+s\s+SET\b', mask[:fact.start()] if fact else '', re.I))
    materialize = re.search(r'\bINTO\s+#document_sales\b', mask, re.I)
    if fact is None or materialize is None or len(updates) != 2:
        return definition
    first, second = updates
    segments = [(first.start(), second.start()), (second.start(), fact.start())]
    expected = ('#document_sales', 'vPriceHistoryERP')
    edits = []
    for (start, stop), source in zip(segments, expected):
        segment = mask[start:stop]
        if not re.search(r'\bFROM\s+' + SALES + r'\s+s\b', segment, re.I) or source.casefold() not in segment.casefold():
            return definition
        # Insert after the JOIN's final ON condition, before the next statement.
        # For the discount update the next statement is a local DECLARE/drop.
        boundaries = list(re.finditer(r'\b(?:DECLARE|IF|DROP)\b|;', segment, re.I))
        end = start + boundaries[0].start() if boundaries else stop
        body = mask[start:end]
        if re.search(r'\bWHERE\b', body, re.I):
            return definition
        edits.append((end, end, '\nWHERE YEAR(s.ClosedDealDate)=@FactYear;\n'))
    # Locate both inner ROW_NUMBER queries and restrict whole partitions before
    # ranking. EXISTS cannot multiply rows, even with repeated sales keys.
    preparation = mask[:first.start()]
    branches = list(re.finditer(r'\bFROM\s*\(', preparation, re.I))
    if len(branches) != 2:
        return definition
    for branch, alias, product in zip(branches, ('rtu', 'kr'), ('rtut', 'krt')):
        opening = preparation.index('(', branch.start())
        end = closing(preparation, opening)
        body = preparation[opening:end]
        if (not re.search(r'\bROW_NUMBER\s*\(', body, re.I)
                or not re.search(r'\b' + alias + r'\.Дата\b', body, re.I)
                or not re.search(r'\b' + product + r'\.Номенклатура\b', body, re.I)
                or re.search(r'\bWHERE\b', body, re.I)):
            return definition
        edits.append((end, end, f'''\nWHERE EXISTS (SELECT 1 FROM #portal_sales_keys pk
 WHERE pk.DocumentKey=DataWH.dbo.makeDocumentKeyDate({alias}.Дата,{alias}.Номер)
 AND pk.ModelCodeERP={product}.Номенклатура)\n'''))
    # OBJECT_ID IF EXISTS may contain SELECT before the materialization. Use the
    # outer SELECT that contains INTO #document_sales instead.
    selects = list(re.finditer(r'\bSELECT\b', preparation[:materialize.start()], re.I))
    if not selects:
        return definition
    point = selects[-1].start()
    setup = f'''{MARKER}
IF OBJECT_ID('tempdb..#portal_sales_keys') IS NOT NULL DROP TABLE #portal_sales_keys;
SELECT DISTINCT DocumentKey,ModelCodeERP INTO #portal_sales_keys
FROM DataWH.dbo.Sales WHERE YEAR(ClosedDealDate)=@FactYear;
-- Avoid index key length/type errors on installations with wider columns.
IF (SELECT SUM(CASE WHEN max_length<0 OR system_type_id IN (34,35,99,241)
 THEN 10000 ELSE max_length END) FROM tempdb.sys.columns
 WHERE object_id=OBJECT_ID('tempdb..#portal_sales_keys')) BETWEEN 1 AND 900
 EXEC(N'CREATE INDEX IX_portal_sales_keys ON #portal_sales_keys(DocumentKey,ModelCodeERP)');
'''
    edits.append((point, point, setup))
    for start, stop, value in sorted(edits, reverse=True):
        definition = definition[:start] + value + definition[stop:]
    return definition
