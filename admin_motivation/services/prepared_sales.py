"""One daily, atomic prepared Sales snapshot for all portal fact calculations."""
import re
from django.core.exceptions import ValidationError
from ..conns import connect_database
from .pilot_formula import mask_sql
from .calculation_sales_isolation import WORKING_TABLE

JOB='PortalMotivation_PrepareSales'
PROC='motivation.PortalPrepareSales'
TABLE='DataWH.motivation.PortalPreparedSales'
RULE='prepared-sales-since-2025-v1'
ORIGINAL_AUTHOR='PortalOriginalDealAuthor'
MARKER='-- Portal: consume the published prepared sales snapshot.'
SCHEMA="""(SELECT column_id,name,system_type_id,user_type_id,max_length,precision,scale,
 is_nullable,is_identity,is_computed,collation_name FROM sys.columns
 WHERE object_id=OBJECT_ID(N'dbo.Sales') ORDER BY column_id FOR XML PATH('column'))"""
DDL="""
IF SCHEMA_ID(N'motivation') IS NULL EXEC(N'CREATE SCHEMA motivation');
IF OBJECT_ID(N'motivation.PortalPreparedSalesRun',N'U') IS NULL
 CREATE TABLE motivation.PortalPreparedSalesRun(
 RunId uniqueidentifier NOT NULL PRIMARY KEY,StartedAt datetime2 NOT NULL,
 CompletedAt datetime2 NULL,State_ nvarchar(20) NOT NULL,Stage_ nvarchar(100) NOT NULL,
 Rule_ nvarchar(100) NOT NULL,PreparedFrom date NOT NULL,PreparedTo date NOT NULL,
 RowCount_ bigint NULL,SourceSchema nvarchar(max) NULL,ErrorNumber_ int NULL);
IF OBJECT_ID(N'motivation.PortalPreparedSalesActive',N'U') IS NULL
 CREATE TABLE motivation.PortalPreparedSalesActive(Id int NOT NULL PRIMARY KEY CHECK(Id=1),
 RunId uniqueidentifier NOT NULL);
IF OBJECT_ID(N'motivation.PortalPreparedSalesConflict',N'U') IS NULL
 CREATE TABLE motivation.PortalPreparedSalesConflict(RunId uniqueidentifier NOT NULL,
 DocumentKey nvarchar(max) NULL,ProductXml nvarchar(max) NULL,VariantsXml nvarchar(max) NULL);
"""



# Remove only an incomplete delivery row covered by a real, more complete row
# with identical price/discount/author inputs. Never synthesize a new tuple or
# rank contradictory filled values by arbitrary document reference.
DOCUMENT_DOMINANCE = """
DELETE d FROM #portal_document_sales_values d
WHERE EXISTS (SELECT 1 FROM #portal_document_sales_values b
 WHERE b.DocumentKey=d.DocumentKey AND b.Номенклатура=d.Номенклатура
 AND b.ВидЦены=d.ВидЦены AND b.ДаичиТипСкидкиНаДату=d.ДаичиТипСкидкиНаДату
 AND b.даичи_ВидЦеныВходнаяСтоимостьНаименование=d.даичи_ВидЦеныВходнаяСтоимостьНаименование
 AND (b.Даичи_АвторСделкиНаименование=d.Даичи_АвторСделкиНаименование
  OR (b.Даичи_АвторСделкиНаименование IS NULL AND d.Даичи_АвторСделкиНаименование IS NULL))
 AND (NULLIF(d.СпособДоставки,'') IS NULL OR d.СпособДоставки=b.СпособДоставки)
 AND (NULLIF(d.[даичи_УсловияДоставки],'') IS NULL OR d.[даичи_УсловияДоставки]=b.[даичи_УсловияДоставки])
 AND ((NULLIF(d.СпособДоставки,'') IS NULL AND NULLIF(b.СпособДоставки,'') IS NOT NULL)
  OR (NULLIF(d.[даичи_УсловияДоставки],'') IS NULL AND NULLIF(b.[даичи_УсловияДоставки],'') IS NOT NULL)));
"""

DOCUMENT_VALUES = """
SELECT DISTINCT DocumentKey,Номенклатура,
 COALESCE(ВидЦены,'') AS ВидЦены,
 COALESCE(ДаичиТипСкидкиНаДату,'') AS ДаичиТипСкидкиНаДату,
 COALESCE(даичи_ВидЦеныВходнаяСтоимостьНаименование,'') AS даичи_ВидЦеныВходнаяСтоимостьНаименование,
 Даичи_АвторСделкиНаименование,ISNULL(СпособДоставки,'') AS СпособДоставки,
 ISNULL([даичи_УсловияДоставки],'') AS [даичи_УсловияДоставки]
INTO #portal_document_sales_values
FROM #document_sales;
""" + DOCUMENT_DOMINANCE + """
IF EXISTS (SELECT 1 FROM #portal_document_sales_values GROUP BY DocumentKey,Номенклатура HAVING COUNT_BIG(*) > 1)
BEGIN
 INSERT INTO motivation.PortalPreparedSalesConflict(RunId,DocumentKey,ProductXml,VariantsXml)
 SELECT TOP(20) @RunId,CONVERT(nvarchar(max),k.DocumentKey),
  (SELECT k.Номенклатура AS value FOR XML PATH('product'),BINARY BASE64),
  (SELECT v.Ссылка AS DocumentReference,v.ROW_ID AS RowId,
   v.ВидЦены AS PriceType,v.ДаичиТипСкидкиНаДату AS DiscountType,
   v.даичи_ВидЦеныВходнаяСтоимостьНаименование AS EntryPriceType,
   v.Даичи_АвторСделкиНаименование AS DealAuthor,v.СпособДоставки AS DeliveryMethod,
   v.[даичи_УсловияДоставки] AS DeliveryTerms
   FROM #document_sales v WHERE v.DocumentKey=k.DocumentKey AND v.Номенклатура=k.Номенклатура
   FOR XML PATH('variant'),BINARY BASE64)
 FROM (SELECT DocumentKey,Номенклатура FROM #portal_document_sales_values
  GROUP BY DocumentKey,Номенклатура HAVING COUNT_BIG(*) > 1) k ORDER BY k.DocumentKey;
 DECLARE @ConflictDocument nvarchar(512),@ConflictMessage nvarchar(2048);
 SELECT TOP(1) @ConflictDocument=CONVERT(nvarchar(512),DocumentKey)
 FROM #portal_document_sales_values GROUP BY DocumentKey,Номенклатура HAVING COUNT_BIG(*) > 1 ORDER BY DocumentKey;
 SET @ConflictMessage=N'Разные атрибуты документа и товара: '+COALESCE(@ConflictDocument,N'?')
  +N'. Примеры сохранены в motivation.PortalPreparedSalesConflict для текущего RunId';
 THROW 50005,@ConflictMessage,1;
END;
IF (SELECT SUM(CASE WHEN max_length<0 OR system_type_id IN (34,35,99,241)
 THEN 10000 ELSE max_length END) FROM tempdb.sys.columns
 WHERE object_id=OBJECT_ID('tempdb..#portal_document_sales_values') AND name IN (N'DocumentKey',N'Номенклатура')) BETWEEN 1 AND 900
 EXEC(N'CREATE INDEX IX_document_sales_match ON #portal_document_sales_values(DocumentKey,Номенклатура)');
"""


def preparation_span(definition):
    """Recognize only the isolated, supported two-update preparation block."""
    code=mask_sql(definition)
    setup=re.search(r'SELECT\s+\*\s+INTO\s+'+WORKING_TABLE+r'\s+FROM\s+DataWH\.dbo\.Sales\s+WHERE[^;]+;',code,re.I)
    fact=re.search(r';\s*WITH\s+fact\s+AS\s*\(',code,re.I)
    if not setup or not fact or setup.end()>fact.start():
        raise ValidationError('Не распознана граница общей подготовки продаж. Расчёт не изменён.')
    updates=list(re.finditer(r'\bUPDATE\s+s\s+SET\b',code[setup.end():fact.start()],re.I))
    if len(updates)!=2:raise ValidationError('Для общего набора нужны два известных обновления продаж.')
    second=setup.end()+updates[1].start()
    boundary=re.search(r';|\b(?:DECLARE|IF|DROP)\b',code[second:fact.start()],re.I)
    if not boundary:raise ValidationError('Не найдена граница расчёта скидок.')
    stop=second+boundary.start()+(boundary.group()==';')
    if re.search(r'\bDECLARE\b',code[setup.end():stop],re.I):
        raise ValidationError('Подготовка содержит дополнительные параметры; нужна проверка совместимости.')
    if re.search(r'\b(?:FROM|JOIN|UPDATE|INTO)\s+#(?:document_sales|portal_sales_keys)\b',code[stop:],re.I):
        raise ValidationError('Расчёт использует временные документы после подготовки; нужна проверка совместимости.')
    return setup,stop


def canonical_preparation():
    from .recalculation_2025_sql import template_source
    from .calculation_performance import patch as scope
    from .calculation_sales_isolation import patch as isolate
    source=isolate(scope(template_source()))
    setup,stop=preparation_span(source)
    return source[setup.start():stop]


def signature(sql):
    # Ignore formatting/comments and identifier brackets; preserve literal contents.
    sql=re.sub(r"s\.Даичи_АвторСделкиНаименование\s*=\s*COALESCE\(NULLIF\(ds\.Даичи_АвторСделкиНаименование,N''\),s\.Даичи_АвторСделкиНаименование\)",
        's.Даичи_АвторСделкиНаименование = ds.Даичи_АвторСделкиНаименование',sql,flags=re.I)
    tokens=re.findall(r"N?'(?:''|[^'])*'|--[^\n]*|/\*[\s\S]*?\*/|\[[^\]]+\]|\w+|[^\s]",sql,re.S)
    return tuple(t if re.match(r"N?'",t) else t.strip('[]').casefold() for t in tokens if not t.startswith(('--','/*')))


def patch(definition, *, historical_author=False, cursor=None):
    if MARKER in definition:return definition
    from .calculation_sales_isolation import SALES
    if WORKING_TABLE.casefold() not in mask_sql(definition).casefold() and not re.search(SALES,mask_sql(definition),re.I):
        return definition  # A calculation with no Sales input needs no prepared copy.
    setup,stop=preparation_span(definition)
    body=definition[setup.end():stop]
    canonical=canonical_preparation();expected,end=preparation_span(canonical+'\n;WITH fact AS (SELECT 1)')
    from .prepared_sales_compatibility import compatible
    if not compatible(body,canonical[expected.end():end],cursor):
        raise ValidationError('Подготовка скидок в исходной процедуре отличается от общего набора. Требуется проверка совместимости; исходная процедура не изменена.')
    original_where=definition[setup.start():setup.end()]
    where=re.search(r'\bWHERE\s+([^;]+);',original_where,re.I)[1]
    where=where.replace('AND YEAR(ClosedDealDate)=@FactYear',
        'AND ClosedDealDate<DATEFROMPARTS(@FactYear+1,1,1) AND YEAR(ClosedDealDate)=@FactYear')
    # Scope is applied while reading the common set, preserving each variant's branches.
    replacement=f"""{MARKER}
DECLARE @PortalPreparedRun uniqueidentifier,@PortalSalesSchema nvarchar(max)={SCHEMA};
SELECT @PortalPreparedRun=r.RunId FROM motivation.PortalPreparedSalesActive a
 JOIN motivation.PortalPreparedSalesRun r ON r.RunId=a.RunId
 WHERE a.Id=1 AND r.State_=N'completed' AND r.Rule_=N'{RULE}'
 AND r.PreparedFrom<=DATEFROMPARTS(@FactYear,1,1)
 AND r.PreparedTo>=DATEFROMPARTS(@FactYear+1,1,1) AND r.SourceSchema=@PortalSalesSchema;
IF @FactYear<2025 OR @PortalPreparedRun IS NULL OR OBJECT_ID(N'motivation.PortalPreparedSales',N'U') IS NULL
 THROW 50040,N'Нет подходящего готового набора продаж. В Центре расчётов запустите подготовку факта с 2025 года',1;
SELECT * INTO {WORKING_TABLE} FROM {TABLE} WHERE {where};
"""
    if historical_author:
        replacement+=f"UPDATE {WORKING_TABLE} SET Даичи_АвторСделкиНаименование=COALESCE(NULLIF(Даичи_АвторСделкиНаименование,N''),{ORIGINAL_AUTHOR});\n"
    # Remove the complete old enrichment, including creation of document/key tables.
    return definition[:setup.start()]+replacement+definition[stop:]


def procedure_sql():
    body=canonical_preparation()
    body=body.replace('DataWH.motivation.PortalCalculationSubdivisions','DataWH.dbo.Subdivisions')
    body=body.replace('ClosedDealDate>=DATEFROMPARTS(@FactYear,1,1) AND YEAR(ClosedDealDate)=@FactYear',
        "ClosedDealDate>='20250101' AND ClosedDealDate<@PreparedTo")
    body=body.replace('YEAR(ClosedDealDate)=@FactYear',"ClosedDealDate>='20250101' AND ClosedDealDate<@PreparedTo")
    body=body.replace('YEAR(s.ClosedDealDate)=@FactYear',"s.ClosedDealDate>='20250101' AND s.ClosedDealDate<@PreparedTo")
    body=body.replace(f'SELECT * INTO {WORKING_TABLE}\nFROM DataWH.dbo.Sales',
        f'SELECT s.*,CONVERT(nvarchar(max),s.Даичи_АвторСделкиНаименование) AS {ORIGINAL_AUTHOR} INTO {WORKING_TABLE}\nFROM DataWH.dbo.Sales s CROSS JOIN (SELECT 1 AS SchemaOnly) schema_only',1)
    guard="""IF EXISTS (SELECT 1 FROM #document_sales GROUP BY DocumentKey, Номенклатура HAVING COUNT_BIG(*) > 1)
    THROW 50005, N'Неоднозначное соответствие документа и товара.', 1;
CREATE INDEX IX_document_sales_match ON #document_sales(DocumentKey, Номенклатура);"""
    if body.count(guard)!=1:
        raise ValidationError('Не распознана проверка однозначности документов подготовки.')
    body=body.replace(guard,DOCUMENT_VALUES,1)
    body=body.replace('LEFT JOIN #document_sales ds','LEFT JOIN #portal_document_sales_values ds')
    # Progress is committed between stages; publication alone is transactional.
    body=body.replace('SELECT DISTINCT DocumentKey,ModelCodeERP',"UPDATE motivation.PortalPreparedSalesRun SET Stage_=N'Подготовка документов' WHERE RunId=@RunId;\nSELECT DISTINCT DocumentKey,ModelCodeERP",1)
    updates=list(re.finditer(r'\bUPDATE\s+s\s+SET\b',mask_sql(body),re.I))
    for match,label in reversed(list(zip(updates,('Атрибуты документов','Расчёт скидок')))):
        body=body[:match.start()]+f"UPDATE motivation.PortalPreparedSalesRun SET Stage_=N'{label}' WHERE RunId=@RunId;\n"+body[match.start():]
    if '@FactYear' in body:raise ValidationError('В общей подготовке остался фильтр одного года.')
    # Enrich the unpublished staging table directly: avoid copying the entire
    # multi-year set a second time from tempdb before publication.
    body=body.replace(WORKING_TABLE,'DataWH.motivation.PortalPreparedSalesStage')
    return f"""ALTER PROCEDURE {PROC} AS
BEGIN
SET NOCOUNT ON; SET XACT_ABORT ON; SET IMPLICIT_TRANSACTIONS OFF;
IF @@TRANCOUNT<>0 THROW 50040,N'Подготовку нужно запускать вне транзакции',1;
DECLARE @Lock int,@RunId uniqueidentifier=NEWID(),@Rows bigint,
 @PreparedTo date=DATEFROMPARTS(YEAR(GETDATE())+1,1,1),@SourceSchema nvarchar(max)={SCHEMA};
EXEC @Lock=sys.sp_getapplock @Resource=N'PortalMotivationCalculation',@LockMode=N'Exclusive',@LockOwner=N'Session',@LockTimeout=0;
IF @Lock<0 THROW 50040,N'Сейчас выполняется расчёт или подготовка продаж. Дождитесь завершения',1;
BEGIN TRY
 IF EXISTS(SELECT 1 FROM sys.columns WHERE object_id=OBJECT_ID(N'dbo.Sales') AND name=N'{ORIGINAL_AUTHOR}')
  THROW 50040,N'Конфликт служебного поля подготовки продаж',1;
 INSERT INTO motivation.PortalPreparedSalesRun(RunId,StartedAt,State_,Stage_,Rule_,PreparedFrom,PreparedTo,SourceSchema)
 VALUES(@RunId,SYSUTCDATETIME(),N'running',N'Копирование продаж',N'{RULE}','20250101',@PreparedTo,@SourceSchema);
 IF OBJECT_ID(N'motivation.PortalPreparedSalesStage',N'U') IS NOT NULL DROP TABLE motivation.PortalPreparedSalesStage;
 {body}
 SELECT @Rows=COUNT_BIG(*) FROM motivation.PortalPreparedSalesStage;
 IF @Rows=0 THROW 50040,N'Набор продаж с 2025 года пуст: прежний набор сохранён',1;
 IF @SourceSchema<>{SCHEMA} THROW 50040,N'Во время подготовки изменилась структура Sales',1;
 UPDATE motivation.PortalPreparedSalesRun SET Stage_=N'Публикация набора' WHERE RunId=@RunId;
 CREATE INDEX IX_PortalPreparedSales_Date ON motivation.PortalPreparedSalesStage(ClosedDealDate);
 BEGIN TRANSACTION;
 IF OBJECT_ID(N'motivation.PortalPreparedSales',N'U') IS NOT NULL DROP TABLE motivation.PortalPreparedSales;
 EXEC sys.sp_rename N'motivation.PortalPreparedSalesStage',N'PortalPreparedSales';
 UPDATE motivation.PortalPreparedSalesRun SET State_=N'completed',Stage_=N'Готово',CompletedAt=SYSUTCDATETIME(),RowCount_=@Rows WHERE RunId=@RunId;
 DELETE FROM motivation.PortalPreparedSalesActive;
 INSERT INTO motivation.PortalPreparedSalesActive VALUES(1,@RunId);
 COMMIT TRANSACTION;
 EXEC sys.sp_releaseapplock @Resource=N'PortalMotivationCalculation',@LockOwner=N'Session';
END TRY
BEGIN CATCH
 IF XACT_STATE()<>0 ROLLBACK TRANSACTION;
 UPDATE motivation.PortalPreparedSalesRun SET State_=N'failed',Stage_=N'Ошибка подготовки',CompletedAt=SYSUTCDATETIME(),ErrorNumber_=ERROR_NUMBER() WHERE RunId=@RunId;
 EXEC sys.sp_releaseapplock @Resource=N'PortalMotivationCalculation',@LockOwner=N'Session';
 THROW;
END CATCH;
END;"""


def install(cursor):
    cursor.execute(DDL)
    cursor.execute(f"IF OBJECT_ID(N'{PROC}',N'P') IS NULL EXEC(N'CREATE PROCEDURE {PROC} AS RETURN;');")
    cursor.execute(procedure_sql())
    # The daily schedule is fixed at 08:00 Moscow, converted to server-local time.
    cursor.execute(f"""DECLARE @JobId uniqueidentifier,@Time int;
SELECT @JobId=job_id FROM msdb.dbo.sysjobs WHERE name=N'{JOB}';
IF @JobId IS NULL
BEGIN
 EXEC msdb.dbo.sp_add_job @job_name=N'{JOB}',@enabled=1,@job_id=@JobId OUTPUT,@description=N'Общая подготовка факта продаж с 2025 года';
 EXEC msdb.dbo.sp_add_jobstep @job_id=@JobId,@step_name=N'Подготовка продаж',@subsystem=N'TSQL',@database_name=N'DataWH',@flags=4,@command=N'EXEC {PROC};';
 EXEC msdb.dbo.sp_add_jobserver @job_id=@JobId;
END
ELSE
 EXEC msdb.dbo.sp_update_jobstep @job_id=@JobId,@step_id=1,@command=N'EXEC {PROC};';
IF NOT EXISTS(SELECT 1 FROM msdb.dbo.sysjobschedules js JOIN msdb.dbo.sysschedules s ON s.schedule_id=js.schedule_id WHERE js.job_id=@JobId AND s.name=N'{JOB}_0800_Moscow')
BEGIN
 SET @Time=CONVERT(int,REPLACE(CONVERT(varchar(8),DATEADD(minute,DATEPART(TZOFFSET,SYSDATETIMEOFFSET())-180,DATEADD(hour,8,CONVERT(datetime,'19000101',112))),108),':',''));
 EXEC msdb.dbo.sp_add_jobschedule @job_id=@JobId,@name=N'{JOB}_0800_Moscow',@freq_type=4,@freq_interval=1,@active_start_time=@Time;
END;""")


def start(connector=None):
    connection=None
    try:
        connection,cursor=(connector or connect_database)('vm-dwh','DataWH')
        connection.timeout=120
        cursor.execute("DECLARE @r int; EXEC @r=sys.sp_getapplock @Resource=N'PortalMotivationCalculation',@LockMode=N'Exclusive',@LockOwner=N'Session',@LockTimeout=0; IF @r<0 THROW 50040,N'Сейчас выполняется расчёт или подготовка факта',1;")
        install(cursor);connection.commit()
        cursor.execute("EXEC sys.sp_releaseapplock @Resource=N'PortalMotivationCalculation',@LockOwner=N'Session';")
        cursor.execute(f"EXEC msdb.dbo.sp_start_job @job_name=N'{JOB}';")
        connection.commit()
    except Exception:
        if connection is not None:
            connection.rollback()
        raise
    finally:
        if connection is not None:connection.close()


def monitor(connector=None):
    connection=None
    try:
        connection,cursor=(connector or connect_database)('vm-dwh','DataWH');connection.timeout=10
        cursor.execute("SELECT OBJECT_ID(N'motivation.PortalPreparedSalesRun')")
        if not cursor.fetchone()[0]:return {'configured':False,'status':'Не настроено','history':[]}
        cursor.execute("SELECT TOP(10) RunId,StartedAt,CompletedAt,State_,Stage_,RowCount_,ErrorNumber_ FROM motivation.PortalPreparedSalesRun ORDER BY StartedAt DESC")
        rows=cursor.fetchall()
        history=[dict(run_id=str(r[0]),started_at=r[1].isoformat()+'Z',completed_at=r[2].isoformat()+'Z' if r[2] else None,state=r[3],stage=r[4],row_count=r[5],error_number=r[6]) for r in rows]
        cursor.execute("SELECT r.RunId,r.CompletedAt,r.RowCount_ FROM motivation.PortalPreparedSalesActive a JOIN motivation.PortalPreparedSalesRun r ON r.RunId=a.RunId WHERE a.Id=1 AND r.State_=N'completed'")
        ready=cursor.fetchone()
        cursor.execute(f"""SELECT a.start_execution_date,a.stop_execution_date,h.run_status
 FROM msdb.dbo.sysjobs j LEFT JOIN msdb.dbo.sysjobactivity a ON a.job_id=j.job_id
 AND a.session_id=(SELECT MAX(session_id) FROM msdb.dbo.syssessions)
 OUTER APPLY(SELECT TOP(1) run_status FROM msdb.dbo.sysjobhistory WHERE job_id=j.job_id AND step_id=0 ORDER BY instance_id DESC) h
 WHERE j.name=N'{JOB}'""")
        agent=cursor.fetchone();running=bool(agent and agent[0] and not agent[1])
        if history and history[0]['state']=='running' and agent and agent[1]:
            history[0]['state']='interrupted';history[0]['stage']='Подготовка прервана; предыдущий набор сохранён'
        status=history[0]['stage'] if history else 'Набор ещё не подготовлен'
        if running and (not history or history[0]['state']!='running'):status='Запуск подготовки'
        if not running and agent and agent[2]==0 and (not history or history[0]['state']=='completed'):
            status='Последний запуск задания завершился ошибкой. Проверьте историю SQL Agent'
        return {'configured':bool(agent),'status':status,'agent_running':running,'history':history,
            'ready':{'run_id':str(ready[0]),'completed_at':ready[1].isoformat()+'Z','row_count':ready[2]} if ready else None}

    finally:
        if connection is not None:connection.close()
