"""The sole employee-name authority for 2026 iterations is the live Sales field."""
TABLE='DataWH.dbo.Sales'
FIELD='Даичи_АвторСделкиНаименование'
LABEL=TABLE+'.'+FIELD
# Stable invoice/fact dimensions. Author IDs are deliberately not matching keys:
# the upstream author refresh may change those IDs together with the name.
KEYS=('Subdivision','ClosedDealDate','DocumentKey','ModelCodeERP','ProductCode','CustomerKey','DateInvoice','DocumentNumber')


def match(left,right):
    return ' AND '.join(f'({left}.[{field}]={right}.[{field}] OR ({left}.[{field}] IS NULL AND {right}.[{field}] IS NULL))' for field in KEYS)


def refresh_private(table):
    columns=','.join('r.['+field+']' for field in KEYS)
    name=f"COALESCE(NULLIF(LTRIM(RTRIM(r.[{FIELD}])),N''),N'')"
    # The map has one row per source identity. Different names (including a
    # named/blank mixture) are a data conflict, never an arbitrary MIN/TOP pick.
    return f"""SELECT {columns},COUNT(DISTINCT {name}) AS AuthorCount,MIN({name}) AS AuthorName
INTO #iteration_live_sales_authors FROM {TABLE} r
WHERE r.ClosedDealDate>=@DateFrom AND r.ClosedDealDate<@DateTo
AND EXISTS(SELECT 1 FROM {table} current_sale WHERE {match('r','current_sale')})
GROUP BY {columns};
IF EXISTS(SELECT 1 FROM #iteration_live_sales_authors WHERE AuthorCount>1)
 THROW 50040,N'В Sales несколько авторов для одного ключа строки факта. Уточните исходные строки; автор не выбран произвольно.',1;
UPDATE current_sale SET current_sale.[{FIELD}]=COALESCE(live_author.AuthorName,N'')
FROM {table} current_sale LEFT JOIN #iteration_live_sales_authors live_author ON {match('live_author','current_sale')};
"""
