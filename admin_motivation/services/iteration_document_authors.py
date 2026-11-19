"""Resolve missing authors by exact ERP document keys, without multiplying sales."""
KEYS='#iteration_document_author_keys'
AUTHORS='#iteration_document_authors'
AUTHOR="COALESCE(NULLIF(LTRIM(RTRIM(s.Даичи_АвторСделкиНаименование)),N''),NULLIF(LTRIM(RTRIM(s.PortalOriginalDealAuthor)),N''),N'')"
RESOLVED="COALESCE(NULLIF(LTRIM(RTRIM(s.Даичи_АвторСделкиНаименование)),N''),NULLIF(LTRIM(RTRIM(s.PortalOriginalDealAuthor)),N''),CASE WHEN da.AuthorCount=1 THEN da.AuthorName END,N'')"
JOIN=f'LEFT JOIN {AUTHORS} da ON da.DocumentKey=s.DocumentKey'


def create_keys(table):
    # No parameters: the table belongs to the caller's SQL session.
    return f'SELECT TOP(0) s.DocumentKey INTO {KEYS} FROM {table} s;'


def fill_keys(table,predicate):
    return f'INSERT INTO {KEYS}(DocumentKey) SELECT DISTINCT s.DocumentKey FROM {table} s WHERE {AUTHOR}=N\'\' AND ({predicate});'


MAP_SQL=f"""WITH document_authors AS (
 SELECT DataWH.dbo.makeDocumentKeyDate(rtu.Дата,rtu.Номер) AS DocumentKey,
 NULLIF(LTRIM(RTRIM(rtu.Даичи_АвторСделкиНаименование)),N'') AS AuthorName
 FROM DataWH.erp.[Документы.РеализацияТоваровУслуг] rtu
 WHERE EXISTS(SELECT 1 FROM {KEYS} k WHERE k.DocumentKey=DataWH.dbo.makeDocumentKeyDate(rtu.Дата,rtu.Номер))
 UNION ALL
 SELECT DataWH.dbo.makeDocumentKeyDate(kr.Дата,kr.Номер),
 NULLIF(LTRIM(RTRIM(rtu.Даичи_АвторСделкиНаименование)),N'')
 FROM DataWH.erp.[Документы.КорректировкаРеализации] kr
 JOIN DataWH.erp.[Документы.РеализацияТоваровУслуг] rtu ON rtu.Ссылка=kr.ДокументОснование_Многосостав
 WHERE EXISTS(SELECT 1 FROM {KEYS} k WHERE k.DocumentKey=DataWH.dbo.makeDocumentKeyDate(kr.Дата,kr.Номер))
)
SELECT DocumentKey,COUNT(DISTINCT AuthorName) AS AuthorCount,MIN(AuthorName) AS AuthorName
INTO {AUTHORS} FROM document_authors WHERE AuthorName IS NOT NULL GROUP BY DocumentKey;
"""


def enrich_private(table):
    return create_keys(table)+fill_keys(table,'1=1')+MAP_SQL+f"""
UPDATE s SET s.Даичи_АвторСделкиНаименование=da.AuthorName FROM {table} s
JOIN {AUTHORS} da ON da.DocumentKey=s.DocumentKey
WHERE {AUTHOR}=N'' AND da.AuthorCount=1;
"""
