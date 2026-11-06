"""Reuse enriched sales within one locked Agent session, never across runs."""
import re
from .pilot_formula import mask_sql
from .calculation_sales_isolation import MARKER, WORKING_TABLE
from .calculation_pipeline import CACHE, STATE

SHARED_MARKER = '-- Portal: session-local prepared sales reuse.'


def patch(definition, cursor=None):
    if SHARED_MARKER in definition or MARKER not in definition:
        return definition
    if cursor is not None:
        # Explicit INSERT is unsafe for a rowversion column. Keep the original
        # SELECT INTO preparation on such installations or unknown metadata.
        cursor.execute("SELECT COUNT_BIG(*),COALESCE(SUM(CASE WHEN system_type_id=189 THEN 1 ELSE 0 END),0) FROM sys.columns WHERE object_id=OBJECT_ID(N'dbo.Sales')")
        columns = cursor.fetchone()
        if not columns or len(columns) != 2 or not isinstance(columns[0], int) or columns[0] == 0 or columns[1] != 0:
            return definition
    mask = mask_sql(definition)
    setup = re.search(r'SELECT\s+\*\s+INTO\s+'+WORKING_TABLE+
                      r'\s+FROM\s+DataWH\.dbo\.Sales\s+WHERE(?P<where>[^;]+);', mask, re.I)
    fact = re.search(r';\s*WITH\s+fact\s+AS\s*\(', mask, re.I)
    if not setup or not fact:
        return definition
    updates = list(re.finditer(r'\bUPDATE\s+s\s+SET\b', mask[setup.end():fact.start()], re.I))
    if len(updates) != 2:
        return definition
    second = setup.end() + updates[1].start()
    boundary = re.search(r';|\b(?:DECLARE|IF|DROP)\b', mask[second:fact.start()], re.I)
    if not boundary:
        return definition
    end = second + boundary.start() + (1 if boundary.group() == ';' else 0)
    body = definition[setup.end():end]
    # Do not skip declarations consumed by later calculation phases, nor temp
    # sources referenced by the fact query. Unknown helpers simply keep the
    # original preparation; the main projection optimization is independent.
    if (re.search(r'\bDECLARE\b', mask[setup.end():end], re.I)
            or re.search(r'\b(?:FROM|JOIN|UPDATE|INTO)\s+#(?:document_sales|portal_sales_keys)\b', mask[end:], re.I)):
        return definition
    where = definition[setup.start('where'):setup.end('where')]
    replacement = f"""{SHARED_MARKER}
DECLARE @portalPrepareStarted datetime2=SYSUTCDATETIME(), @portalReuseSales bit=0;
IF OBJECT_ID('tempdb..{CACHE}') IS NOT NULL AND OBJECT_ID('tempdb..{STATE}') IS NOT NULL
BEGIN
 IF EXISTS (SELECT 1 FROM {STATE} WHERE FactYear=@FactYear AND Ready=1)
  SET @portalReuseSales=1;
END;
SELECT TOP (0) s.* INTO {WORKING_TABLE} FROM DataWH.dbo.Sales s
CROSS JOIN (SELECT 1 AS SchemaOnly) schema_only;
IF @portalReuseSales=1
 INSERT INTO {WORKING_TABLE} SELECT * FROM {CACHE};
ELSE
 INSERT INTO {WORKING_TABLE} SELECT * FROM DataWH.dbo.Sales WHERE {where};
IF @portalReuseSales=0
BEGIN
{body}
 IF OBJECT_ID('tempdb..{CACHE}') IS NOT NULL AND OBJECT_ID('tempdb..{STATE}') IS NOT NULL
 BEGIN
  IF EXISTS (SELECT 1 FROM {STATE} WHERE FactYear=@FactYear)
  BEGIN
   DELETE FROM {CACHE};
   INSERT INTO {CACHE} SELECT * FROM {WORKING_TABLE};
   UPDATE {STATE} SET Ready=1 WHERE FactYear=@FactYear;
  END;
 END;
END;
PRINT N'Подготовка продаж: '+CONVERT(nvarchar(30),DATEDIFF(MILLISECOND,@portalPrepareStarted,SYSUTCDATETIME()))
 +N' мс; переиспользована: '+CONVERT(nvarchar(1),@portalReuseSales);
"""
    return definition[:setup.start()] + replacement + definition[end:]
