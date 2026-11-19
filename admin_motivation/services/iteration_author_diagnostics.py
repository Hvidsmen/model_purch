"""Read-only investigation of the exact Q4 author cohort; no payout changes."""
from datetime import date,timedelta
from zoneinfo import ZoneInfo
from django.core.exceptions import ValidationError
from django.utils import timezone
from ..conns import connect_database
from .coefficient_iteration_calculation import branch,MOSCOW,TT
from .motivation_exclusions import DEFAULT_RULES,kind_name,normalize,group_sql
from .prepared_sales import RULE as PREPARED_RULE

LABELS={'prepared':'Автор есть в подготовленном факте','original':'Автор есть в снимке исходного Sales',
 'document_author':'Автор найден по ERP-документу','document_not_found':'ERP-документ не найден по DocumentKey',
 'document_author_blank':'ERP-документ найден, но автор пустой','document_author_conflict':'У ключа ERP-документа несколько авторов'}
PLANNING="COALESCE(NULLIF(LTRIM(RTRIM(p.PlanningGroupSalesErp)),N''),N'9. OTHER')"
STATUS="""CASE WHEN COALESCE(s.prepared_author,N'')<>N'' THEN 'prepared'
 WHEN COALESCE(s.original_author,N'')<>N'' THEN 'original'
 WHEN da.DocumentKey IS NULL THEN 'document_not_found'
 WHEN da.AuthorCount=0 THEN 'document_author_blank'
 WHEN da.AuthorCount>1 THEN 'document_author_conflict' ELSE 'document_author' END"""
PROJECTION="""CONVERT(nvarchar(1024),s.DocumentKey) COLLATE DATABASE_DEFAULT AS DocumentKey,
 s.DateInvoice,s.DocumentNumber,s.ModelCodeERP,p.ModelCode,p.PlanningGroupSalesErp,
 NULLIF(LTRIM(RTRIM(s.Даичи_АвторСделкиНаименование)),N'') AS prepared_author,
 NULLIF(LTRIM(RTRIM(s.PortalOriginalDealAuthor)),N'') AS original_author,
 s.Price-s.AmountUSDIt_ITW+s.BallsOuterUSD AS amount"""
FROM="""FROM DataWH.motivation.PortalPreparedSales s
 JOIN DataWH.dbo.Subdivisions sub ON sub.SubdivisionKey=s.Subdivision
 JOIN DataWH.dbo.Product p ON p.ProductCode=s.ProductCode AND p.[Тип номенклатуры]=N'Товар'
 JOIN DataWH.dbo.Customer c ON c.CustomerKey=s.CustomerKey"""
MAP="""WITH docs AS (
 SELECT CONVERT(nvarchar(1024),DataWH.dbo.makeDocumentKeyDate(rtu.Дата,rtu.Номер)) COLLATE DATABASE_DEFAULT AS DocumentKey,
 NULLIF(LTRIM(RTRIM(rtu.Даичи_АвторСделкиНаименование)),N'') COLLATE DATABASE_DEFAULT AS AuthorName
 FROM DataWH.erp.[Документы.РеализацияТоваровУслуг] rtu
 WHERE EXISTS(SELECT 1 FROM #diag_author_keys k WHERE k.DocumentKey=CONVERT(nvarchar(1024),DataWH.dbo.makeDocumentKeyDate(rtu.Дата,rtu.Номер)) COLLATE DATABASE_DEFAULT)
 UNION ALL
 SELECT CONVERT(nvarchar(1024),DataWH.dbo.makeDocumentKeyDate(kr.Дата,kr.Номер)) COLLATE DATABASE_DEFAULT,
 NULLIF(LTRIM(RTRIM(rtu.Даичи_АвторСделкиНаименование)),N'') COLLATE DATABASE_DEFAULT
 FROM DataWH.erp.[Документы.КорректировкаРеализации] kr
 JOIN DataWH.erp.[Документы.РеализацияТоваровУслуг] rtu ON rtu.Ссылка=kr.ДокументОснование_Многосостав
 WHERE EXISTS(SELECT 1 FROM #diag_author_keys k WHERE k.DocumentKey=CONVERT(nvarchar(1024),DataWH.dbo.makeDocumentKeyDate(kr.Дата,kr.Номер)) COLLATE DATABASE_DEFAULT)
)
SELECT DocumentKey,COUNT_BIG(*) AS HeaderCount,COUNT(DISTINCT AuthorName) AS AuthorCount,MIN(AuthorName) AS AuthorName
INTO #diag_author_documents FROM docs GROUP BY DocumentKey;"""
FIELDS="""SELECT SCHEMA_NAME(o.schema_id),o.name,c.name,t.name
 FROM sys.objects o JOIN sys.columns c ON c.object_id=o.object_id JOIN sys.types t ON t.user_type_id=c.user_type_id
 WHERE o.type IN(N'U',N'V') AND o.object_id IN(OBJECT_ID(N'dbo.Sales'),OBJECT_ID(N'motivation.PortalPreparedSales'),
 OBJECT_ID(N'erp.[Документы.РеализацияТоваровУслуг]'),OBJECT_ID(N'erp.[Документы.ЗаказКлиента]'))
 AND (c.name LIKE N'%Автор%' OR c.name LIKE N'%Менеджер%' OR c.name LIKE N'%Ответствен%' OR c.name LIKE N'%Author%' OR c.name LIKE N'%Manager%')
 ORDER BY o.schema_id,o.name,c.column_id"""


def scope(snapshot):
    result=snapshot.results.get('moscow_q4_2026') or {}
    if result.get('state')!='completed':raise ValidationError('Выберите завершённый расчёт IV квартала 2026.')
    source=result.get('source') or {}
    through=date.fromisoformat(source['through']) if source.get('through') else timezone.localtime(snapshot.started_at,ZoneInfo('Europe/Moscow')).date()
    if through<date(2026,10,1):raise ValidationError('В запуске ещё нет факта IV квартала 2026.')
    excluded={str(value).strip().casefold() for value in snapshot.inputs.get('excluded_subdivision_names') or []}
    names=sorted({name for name in snapshot.inputs.get('export_names',{}).values()
                  if branch(name) in (MOSCOW,TT) and name.strip().casefold() not in excluded})
    if not names:raise ValidationError('В снимке запуска не найдены подразделения Москвы и Термотрейда.')
    predicate='s.ClosedDealDate>=? AND s.ClosedDealDate<? AND sub.SubdivisionName IN ('+','.join('?' for _ in names)+')'
    params=[date(2026,10,1),min(date(2027,1,1),through+timedelta(days=1)),*names]
    rules=snapshot.inputs.get('coefficient_exclusions')
    for dimension,kind,value in DEFAULT_RULES if rules is None else rules:
        if kind_name(kind)!='Политики':continue
        if dimension=='group':expression=group_sql(PLANNING)
        elif dimension=='channel':expression="UPPER(LTRIM(RTRIM(COALESCE(sub.Chanel,N''))))"
        else:raise ValidationError('Неизвестный вид исключения в снимке запуска.')
        predicate+=' AND NOT ('+expression+'=?)';params.append(normalize(value,dimension))
    return predicate,params,source


def field_coverage(cursor,fields,predicate,params):
    result=[]
    for schema,table in (('dbo','Sales'),('motivation','PortalPreparedSales')):
        columns=[field['column'] for field in fields if field['schema'].casefold()==schema.casefold() and field['table'].casefold()==table.casefold()
                 and field['type'] in ('varchar','nvarchar','char','nchar','text','ntext')]
        columns.sort(key=lambda name:name not in ('Даичи_АвторСделкиНаименование','PortalOriginalDealAuthor'))
        columns=columns[:12]
        if not columns:continue
        values=["CONVERT(nvarchar(max),s.["+name.replace(']',']]')+"])" for name in columns]
        expressions=["COALESCE(SUM(CASE WHEN NULLIF(LTRIM(RTRIM("+value+")),N'') IS NOT NULL THEN 1 ELSE 0 END),0)" for value in values]
        # Identifier names come from sys.columns and are bracket-escaped. Scope
        # values remain ODBC parameters. Both whitelisted tables are read only.
        source=FROM.replace('DataWH.motivation.PortalPreparedSales','DataWH.'+schema+'.'+table)
        cursor.execute('SELECT COUNT_BIG(*),'+','.join(expressions)+' '+source+' WHERE '+predicate,*params)
        values=cursor.fetchone()
        result.extend({'table':schema+'.'+table,'column':column,'rows':values[0],'nonempty':values[index+1]}
                      for index,column in enumerate(columns))
    return result


def diagnose(snapshot,connector=None):
    predicate,params,frozen_source=scope(snapshot)
    connection=None
    try:
        connection,cursor=(connector or connect_database)('vm-dwh','DataWH');connection.timeout=120
        cursor.execute("SELECT CONVERT(nvarchar(36),r.RunId),r.CompletedAt,r.PreparedFrom,r.PreparedTo,r.Rule_ FROM motivation.PortalPreparedSalesActive a JOIN motivation.PortalPreparedSalesRun r ON r.RunId=a.RunId WHERE a.Id=1 AND r.State_=N'completed'")
        ready=cursor.fetchone()
        if not ready:raise ValidationError('Нет завершённого подготовленного факта. Диагностика не выполнялась.')
        if ready[4]!=PREPARED_RULE or ready[2]>params[0] or ready[3]<params[1]:
            raise ValidationError('Текущий подготовленный факт не покрывает период запуска или имеет несовместимую версию. Сначала обновите подготовленный факт.')
        # DDL has no ODBC parameters; only INSERT binds the selected scope.
        cursor.execute('SET NOCOUNT ON; SELECT TOP(0) '+PROJECTION+' INTO #diag_author_sales '+FROM+';')
        cursor.execute('INSERT INTO #diag_author_sales SELECT '+PROJECTION+' '+FROM+' WHERE '+predicate,*params)
        cursor.execute("""SELECT TOP(20) s.*,CASE WHEN s.DateInvoice IS NOT NULL AND NULLIF(LTRIM(RTRIM(CONVERT(nvarchar(255),s.DocumentNumber))),N'') IS NOT NULL
 THEN CONVERT(nvarchar(1024),DataWH.dbo.makeDocumentKeyDate(s.DateInvoice,s.DocumentNumber)) COLLATE DATABASE_DEFAULT END AS invoice_key
 INTO #diag_author_samples FROM #diag_author_sales s
 WHERE COALESCE(prepared_author,N'')=N'' AND COALESCE(original_author,N'')=N'' ORDER BY ABS(amount) DESC;
 SELECT DocumentKey INTO #diag_author_keys FROM #diag_author_sales WHERE DocumentKey IS NOT NULL
 UNION SELECT invoice_key FROM #diag_author_samples WHERE invoice_key IS NOT NULL;""")
        cursor.execute(MAP)
        cursor.execute('SELECT '+STATUS+',COUNT_BIG(*),SUM(s.amount),COUNT(DISTINCT s.DocumentKey) FROM #diag_author_sales s LEFT JOIN #diag_author_documents da ON da.DocumentKey=s.DocumentKey GROUP BY '+STATUS)
        coverage=[{'status':key,'label':LABELS[key],'rows':count,'amount_usd':amount,'documents':documents} for key,count,amount,documents in cursor.fetchall()]
        cursor.execute("""SELECT TOP(20) s.DocumentKey,s.invoice_key,s.DateInvoice,s.DocumentNumber,s.ModelCodeERP,s.ModelCode,s.PlanningGroupSalesErp,
 s.amount,da.HeaderCount,da.AuthorCount,da.AuthorName,other.HeaderCount,other.AuthorCount,other.AuthorName
 FROM #diag_author_samples s LEFT JOIN #diag_author_documents da ON da.DocumentKey=s.DocumentKey
 LEFT JOIN #diag_author_documents other ON other.DocumentKey=s.invoice_key ORDER BY ABS(s.amount) DESC""")
        fields=('document_key','key_from_invoice_date_number','invoice_date','invoice_number','model_erp','model','planning_group',
                'amount_usd','matched_headers','matched_authors','matched_author','invoice_key_headers','invoice_key_authors','invoice_key_author')
        samples=[dict(zip(fields,row)) for row in cursor.fetchall()]
        cursor.execute(FIELDS)
        author_fields=[{'schema':schema,'table':table,'column':column,'type':kind} for schema,table,column,kind in cursor.fetchall()]
        columns=field_coverage(cursor,author_fields,predicate,params)
        cursor.execute('SELECT CONVERT(nvarchar(36),RunId) FROM motivation.PortalPreparedSalesActive WHERE Id=1')
        current=cursor.fetchone()
        if not current or str(current[0]).casefold()!=str(ready[0]).casefold():raise ValidationError('Подготовленный факт изменился во время проверки. Повторите диагностику.')
        return {'run':snapshot.pk,'from':str(params[0]),'through':str(params[1]-timedelta(days=1)),
                'prepared_run_id':ready[0],'prepared_completed_at':ready[1],
                'prepared_from':ready[2],'prepared_to':ready[3],'prepared_rule':ready[4],
                'saved_prepared_run_id':frozen_source.get('prepared_run_id'),
                'same_prepared_snapshot':str(ready[0]).casefold()==str(frozen_source['prepared_run_id']).casefold() if frozen_source.get('prepared_run_id') else None,
                'saved_named_authors':frozen_source.get('named_authors'),'saved_missing_groups':frozen_source.get('missing_author_rows'),
                'coverage':coverage,'samples':samples,'author_fields':author_fields,'column_coverage':columns,
                'note':'Проверены физические строки факта политиков IV квартала в периоде и исключениях сохранённого запуска, независимо от фильтров экрана. Число строк отличается от числа группировок расчёта. План и результаты не изменены. Ключ по дате/номеру — только диагностическая гипотеза, он не применяется к выплатам.'}
    finally:
        if connection is not None:connection.close()
