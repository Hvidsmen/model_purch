"""Historical policy-author shares: broader scopes never cross subdivisions."""
from .recalculation_2025_plan import ELIGIBLE

BASE_QUERY=f"""SELECT ПодразделениеДокумент,АвторСделки,ГруппаПлановПродаж,ГруппаТоваров,Марка,
 SUM(СуммаОтгрузокUSD) AS AmountUSD FROM #fact
 WHERE LTRIM(RTRIM(ТипКоэф))=N'Политики' AND Год=2025 AND {ELIGIBLE}
 GROUP BY ПодразделениеДокумент,АвторСделки,ГруппаПлановПродаж,ГруппаТоваров,Марка
 HAVING SUM(СуммаОтгрузокUSD)<>0"""
BASE_SQL=BASE_QUERY.replace('FROM #fact','INTO #test2025_share_base FROM #fact',1)+';\n'

# Aggregate the small eligible author grid, not the document fact three times.
BROAD_QUERY='\nUNION ALL\n'.join(
    "SELECT ПодразделениеДокумент,АвторСделки,"+
    ("ГруппаПлановПродаж" if level<3 else "CAST(NULL AS nvarchar(255))")+" AS ГруппаПлановПродаж,"+
    ("ГруппаТоваров" if level<2 else "CAST(NULL AS nvarchar(255))")+" AS ГруппаТоваров,"+
    "CAST(NULL AS nvarchar(255)) AS Марка,SUM(AmountUSD) AS AmountUSD,"+str(level)+" AS AllocationLevel\n"+
    "FROM #test2025_share_base GROUP BY ПодразделениеДокумент,АвторСделки"+
    (",ГруппаПлановПродаж" if level<3 else "")+(",ГруппаТоваров" if level<2 else "")+
    " HAVING SUM(AmountUSD)<>0" for level in (1,2,3))

BROAD_SHARES="""SELECT t.*,CASE WHEN SUM(AmountUSD) OVER
 (PARTITION BY ПодразделениеДокумент,AllocationLevel,ГруппаПлановПродаж,ГруппаТоваров)<>0
 THEN AmountUSD/NULLIF(SUM(AmountUSD) OVER
 (PARTITION BY ПодразделениеДокумент,AllocationLevel,ГруппаПлановПродаж,ГруппаТоваров),0)
 ELSE CASE WHEN AmountUSD>0 THEN AmountUSD ELSE 0 END/NULLIF(
 SUM(CASE WHEN AmountUSD>0 THEN AmountUSD ELSE 0 END) OVER
 (PARTITION BY ПодразделениеДокумент,AllocationLevel,ГруппаПлановПродаж,ГруппаТоваров),0)
 END AS Percent_ FROM ("""+BROAD_QUERY+") t"

APPEND_SHARES=BASE_SQL+"""
INSERT INTO #percent_plan_menger
 (ПодразделениеДокумент,АвторСделки,ГруппаПлановПродаж,ГруппаТоваров,Марка,AmountUSD,AllocationLevel,Percent_)
"""+BROAD_SHARES+""";
IF (SELECT SUM(CASE WHEN max_length<0 OR system_type_id IN (34,35,99,241)
 THEN 10000 ELSE max_length END) FROM tempdb.sys.columns
 WHERE object_id=OBJECT_ID('tempdb..#percent_plan_menger')
 AND name IN (N'ПодразделениеДокумент',N'AllocationLevel')) BETWEEN 1 AND 900
 EXEC(N'CREATE INDEX IX_test2025_author_share_scope ON #percent_plan_menger(ПодразделениеДокумент,AllocationLevel)');
"""

# First choose one whole scope; do not choose a different level per author.
# A matching scope with zero signed total already uses positive author nets.
SCOPE_MATCH="""{alias}.ПодразделениеДокумент=s.SubdivisionKey
 AND ({alias}.AllocationLevel=3 OR {alias}.ГруппаПлановПродаж=sc.PGSales)
 AND ({alias}.AllocationLevel>=2 OR {alias}.ГруппаТоваров=sc.Group_)
 AND ({alias}.AllocationLevel>=1 OR {alias}.Марка=sc.Brand)"""

SHARE_JOIN=""" OUTER APPLY (SELECT TOP(1) pick.AllocationLevel FROM #percent_plan_menger pick
 WHERE """+SCOPE_MATCH.format(alias='pick')+""" AND pick.Percent_ IS NOT NULL
 ORDER BY pick.AllocationLevel) selected_share
 LEFT JOIN #percent_plan_menger pm ON pm.AllocationLevel=selected_share.AllocationLevel
 AND """+SCOPE_MATCH.format(alias='pm')+' '
