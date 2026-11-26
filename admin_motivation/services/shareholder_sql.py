"""Adapt installed, prepared-fact portal procedures into session-local batches."""
import re
from hashlib import sha256
from django.core.exceptions import ValidationError
from .pilot_formula import mask_sql
from .calculation_sales_isolation import WORKING_TABLE
from .prepared_sales import MARKER

OUTPUTS=('Fact','PlanFact','ReportMotivationByDealAuthor','AnnualAccrual')
TEMP_OUTPUTS=('#share_fact','#share_plan_fact','#share_report','#share_annual')
AUTHOR='Даичи_АвторСделкиНаименование'
MONEY=('Price','PriceRUR','AmountUSDIt_ITW','BallsOuterUSD','AmountExwAEUSD')

ALLOCATION_SQL=r"""
-- Split additive transaction amounts only; discount percentages and identities stay intact.
SELECT s.*,ROW_NUMBER() OVER(ORDER BY (SELECT NULL)) AS ShareSourceRow,
 sub.SubdivisionName AS ShareSourceBranch
INTO #share_raw FROM #portal_motivation_sales s
JOIN dbo.Subdivisions sub ON sub.SubdivisionKey=s.Subdivision;
SELECT m.*,ROW_NUMBER() OVER(PARTITION BY SourceBranch,OriginalAuthor ORDER BY Weight_ DESC,AssignedAuthor) AS ShareRank
INTO #share_map FROM #share_author_map m;
UPDATE m SET Weight_=1-COALESCE((SELECT SUM(other.Weight_) FROM #share_map other
 WHERE other.SourceBranch=m.SourceBranch AND other.OriginalAuthor=m.OriginalAuthor AND other.ShareRank>1),0)
FROM #share_map m WHERE ShareRank=1;
SELECT TOP(0) s.* INTO #share_transformed FROM #portal_motivation_sales s CROSS JOIN (SELECT 1 AS dummy) d;
ALTER TABLE #share_transformed ADD ShareOriginalAuthor nvarchar(255),ShareSourceRow bigint,
 ShareSourceBranch nvarchar(255),ShareWeight decimal(38,24);
DECLARE @ShareColumns nvarchar(max),@ShareProjection nvarchar(max),@ShareSQL nvarchar(max);
SELECT @ShareColumns=STUFF((SELECT N','+QUOTENAME(c.name) FROM tempdb.sys.columns c
 WHERE c.object_id=OBJECT_ID('tempdb..#portal_motivation_sales') ORDER BY c.column_id
 FOR XML PATH(''),TYPE).value('.','nvarchar(max)'),1,1,N'');
SELECT @ShareProjection=STUFF((SELECT N','+CASE
 WHEN c.name=N'Даичи_АвторСделкиНаименование' THEN N'COALESCE(m.AssignedAuthor,s.'+QUOTENAME(c.name)+N')'
 WHEN c.name IN(N'Price',N'PriceRUR',N'AmountUSDIt_ITW',N'BallsOuterUSD',N'AmountExwAEUSD') THEN
 CASE WHEN c.name=N'PriceRUR' THEN N'CASE WHEN s.Price=0 THEN s.PriceRUR ELSE ' ELSE N'' END+
 N'CASE WHEN COALESCE(m.ShareRank,1)=1 THEN s.'+QUOTENAME(c.name)+
 N'-SUM(CASE WHEN m.ShareRank>1 THEN s.'+QUOTENAME(c.name)+N'*m.Weight_ ELSE 0 END) OVER(PARTITION BY s.ShareSourceRow) ELSE s.'+
 QUOTENAME(c.name)+N'*m.Weight_ END'+CASE WHEN c.name=N'PriceRUR' THEN N' END' ELSE N'' END
 ELSE N's.'+QUOTENAME(c.name) END
 FROM tempdb.sys.columns c WHERE c.object_id=OBJECT_ID('tempdb..#portal_motivation_sales') ORDER BY c.column_id
 FOR XML PATH(''),TYPE).value('.','nvarchar(max)'),1,1,N'');
SET @ShareSQL=N'INSERT INTO #share_transformed ('+@ShareColumns+
 N',ShareOriginalAuthor,ShareSourceRow,ShareSourceBranch,ShareWeight) SELECT '+@ShareProjection+
 N',s.[Даичи_АвторСделкиНаименование],s.ShareSourceRow,s.ShareSourceBranch,COALESCE(m.Weight_,1) FROM #share_raw s LEFT JOIN #share_map m ON m.SourceBranch=s.ShareSourceBranch AND m.OriginalAuthor=COALESCE(LTRIM(RTRIM(s.[Даичи_АвторСделкиНаименование])),N'''')';
EXEC sys.sp_executesql @ShareSQL;
IF EXISTS(
 SELECT ShareSourceBranch FROM (
  SELECT ShareSourceBranch,CAST(Price-AmountUSDIt_ITW+BallsOuterUSD AS decimal(38,8)) AS AmountUSD_,
   CAST((Price-AmountUSDIt_ITW+BallsOuterUSD)*PriceRUR/IIF(Price=0,1,Price) AS decimal(38,8)) AS AmountRUB_ FROM #share_raw
  UNION ALL
  SELECT ShareSourceBranch,-CAST(Price-AmountUSDIt_ITW+BallsOuterUSD AS decimal(38,8)),
   -CAST((Price-AmountUSDIt_ITW+BallsOuterUSD)*PriceRUR/IIF(Price=0,1,Price) AS decimal(38,8)) FROM #share_transformed
 ) conservation GROUP BY ShareSourceBranch
 HAVING ABS(COALESCE(SUM(AmountUSD_),0))>0.01 OR ABS(COALESCE(SUM(AmountRUB_),0))>0.01
)
 THROW 50071,N'Перераспределение не сохранило оборот USD/RUB. Результаты не опубликованы.',1;
"""


def private_sql(definition,mode,run_id,*,original_rules=False,regional_policy=False,planning_groups=False,group_table='#share_group_rules',preserve_main_scope=False):
    from .main_formula_scope import shareholder_source
    from .curator_sql import strip
    definition=strip(definition) if definition else definition
    if not preserve_main_scope:definition=shareholder_source(definition)
    if mode not in ('combined','separate'):raise ValidationError('Неизвестный сценарий.')
    if not definition or MARKER not in definition:raise ValidationError('Сначала обновите существующее задание «Все кварталы 2026»: нужна процедура с подготовленным фактом.')
    suffix='All2026' if mode=='combined' else 'Separate2026'
    proc='PortalCalc'+suffix
    header=re.search(r'\b(?:CREATE\s+(?:OR\s+ALTER\s+)?|ALTER\s+)PROC(?:EDURE)?\s+(?:\[?motivation\]?\s*\.\s*)?\[?'+proc+r'\]?(?!\w)[\s\S]*?\bAS\b',definition,re.I)
    if not header or not all(token in header[0].casefold() for token in ('@factyear','@planversion','@quartershare','@yearshare','@payoutthreshold')):
        raise ValidationError('Не распознаны параметры установленной процедуры '+proc)
    source=definition[:header.start()]+definition[header.end():]
    for table,target in zip(OUTPUTS,TEMP_OUTPUTS):
        # OBJECT_ID needs tempdb scope; actual table references must remain bare #names.
        source=re.sub(r"N?'(?:DataWH\.)?motivation\."+re.escape(table+suffix)+r"'",lambda m:"N'tempdb.."+target+"'",source,flags=re.I)
        source=re.sub(r'(?:(?:\[?DataWH\]?\s*\.\s*)?\[?motivation\]?\s*\.\s*)\[?'+re.escape(table+suffix)+r'\]?(?!\w)',target,source,flags=re.I)
    if any(re.search(r'\bmotivation\.\[?'+name+r'(?:All2026|Separate2026)?\b',mask_sql(source),re.I) for name in OUTPUTS):
        raise ValidationError('Не удалось изолировать все таблицы расчёта.')
    source=re.sub(r'(?:(?:\[?DataWH\]?\s*\.\s*)?\[?motivation\]?\s*\.\s*)\[?Portal(?:Calculation|Separate)Subdivisions\]?(?!\w)', '#share_subdivisions',source,flags=re.I)
    # Audit needs the complete prepared schema, even if the installed consumer is narrowed.
    source=re.sub(r'SELECT\s+[^;]*?\s+INTO\s+#portal_motivation_sales\s+FROM\s+DataWH\.motivation\.PortalPreparedSales', 'SELECT * INTO #portal_motivation_sales FROM DataWH.motivation.PortalPreparedSales', source, flags=re.I)
    source,count=re.subn(r'(FROM\s+DataWH\.motivation\.PortalPreparedSales\s+WHERE\s+)',r'\1ClosedDealDate<@ShareCutoff AND ',source,flags=re.I)
    if count!=1:raise ValidationError('Не найдена единственная граница даты подготовленного факта.')
    from .shareholder_write_guard import validate_private_writes
    validate_private_writes(source)
    code=mask_sql(source)
    if re.search(r'\bMERGE\b',code,re.I):raise ValidationError('Неизвестная запись MERGE в установленной процедуре.')
    index_targets=re.findall(r'\bCREATE\s+(?:UNIQUE\s+)?(?:(?:CLUSTERED|NONCLUSTERED)\s+)?INDEX\s+\S+\s+ON\s+([#\w.\[\]]+)',code,re.I)
    if any(not target.startswith('#') for target in index_targets):raise ValidationError('Индекс вне временной таблицы не разрешён.')
    calls=re.findall(r'\bEXEC(?:UTE)?\s+(?:@\w+\s*=\s*)?([\w.\[\]]+)',code,re.I)
    if any(call.replace('[','').replace(']','').casefold() not in ('sys.sp_getapplock','sys.sp_releaseapplock','sys.sp_set_session_context') for call in calls):
        raise ValidationError('Неизвестный вызов процедуры в расчёте. Нужна проверка совместимости.')
    fact=re.search(r';\s*WITH\s+fact\s+AS\s*\(',mask_sql(source),re.I)
    if not fact:raise ValidationError('Не найдена граница применения коэффициентов.')
    tail=source[fact.start():].replace(WORKING_TABLE,'#share_transformed')
    source=source[:fact.start()]+ALLOCATION_SQL+tail
    source=source.replace("N'motivation.sp_CalcBySub'","N'Shareholder:"+str(run_id)+"'")
    if re.search(r'\b(?:CREATE|ALTER)\s+PROC',mask_sql(source),re.I):raise ValidationError('Пакет не должен изменять процедуру.')
    prefix='''SET NOCOUNT ON; SET XACT_ABORT ON;
DECLARE @FactYear int,@PlanVersion nvarchar(500),@QuarterShare decimal(9,8),@YearShare decimal(9,8),@PayoutThreshold decimal(9,8),@ShareCutoff date;
SELECT @FactYear=FactYear,@PlanVersion=PlanVersion,@QuarterShare=QuarterShare,@YearShare=YearShare,@PayoutThreshold=PayoutThreshold,@ShareCutoff=Cutoff FROM #share_parameters;
SELECT * INTO #share_subdivisions FROM dbo.Subdivisions WHERE SubdivisionName IN(N'Д-Москва',N'ТЕРМОТРЕЙД');
'''
    if mode=='combined':prefix+="UPDATE #share_subdivisions SET SubdivisionName=N'Д-Москва' WHERE SubdivisionName=N'ТЕРМОТРЕЙД';\n"
    if planning_groups or '__PORTAL_GROUP_RULES__' in source:
        from .planning_group_rules import patch as patch_groups,TABLE
        source=patch_groups(source,table=group_table,optimized=True).replace(TABLE,group_table)
        if regional_policy:
            from .shareholder_regions import REGION_SQL
            fields='DateFrom,DateTo,Subdivision,PGSales,TypeCoeff,K0,K1,K2,K3,K4'
            rates='(SELECT '+fields+' FROM DataWH.motivation.vSubdivisionMotiveCoeffTo WHERE Subdivision NOT IN('+REGION_SQL+") OR LTRIM(RTRIM(TypeCoeff))<>N'Политики' UNION ALL SELECT "+fields+' FROM #share_region_coefficients)'
            source=source.replace('DataWH.motivation.vSubdivisionMotiveCoeffTo grc',rates+' grc').replace('#coeff_source grc',rates+' grc')
            for relation in ('DataWH.motivation.vSubdivisionMotiveCoeffTo','#coeff_source'):
                source=source.replace(relation+'\n-- __PORTAL_GROUP_MIN_SOURCE_END__',rates+'\n-- __PORTAL_GROUP_MIN_SOURCE_END__')
    if regional_policy:
        from .shareholder_regions import patch,BRANCH_SQL
        source=patch(source)
        prefix=prefix.replace("N'Д-Москва',N'ТЕРМОТРЕЙД'",BRANCH_SQL)
    if planning_groups:
        from .group_rule_performance import materialize
        source=materialize(source,table=group_table)
    if original_rules:
        from .shareholder_original_rules import restore
        source=restore(source)
    source=prefix+source+"\nSELECT @PortalPreparedRun AS SharePreparedRun;"
    return source


def fingerprint(source):return sha256(source.encode('utf-8')).hexdigest()


def temporary_tables(source):
    code=mask_sql(source)
    return sorted(set(re.findall(r'\bINTO\s+(#[\w]+)',code,re.I))|{'#share_map','#share_raw','#share_transformed'})
