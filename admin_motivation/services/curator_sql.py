"""Run each curator/physical branch through an isolated copy of the director rules."""
import re
from uuid import uuid4
from django.core.exceptions import ValidationError
from .shareholder_sql import private_sql,ALLOCATION_SQL
from .calculation_variants import isolate

START='-- Portal curator calculations begin.'
END='-- Portal curator calculations end.'
TABLE='motivation.PortalCuratorAssignments'


def strip(definition):
    if START not in definition:return definition
    start=definition.index(START);end=definition.index(END,start)+len(END)
    return definition[:start]+definition[end:]


def export_assignments(cursor):
    from ..models import CuratorEmployee
    cursor.execute(f'''IF OBJECT_ID(N'{TABLE}',N'U') IS NULL CREATE TABLE {TABLE}(
 EmployeeCode nvarchar(64) NOT NULL,Subdivision nvarchar(255) NOT NULL,FullName nvarchar(255) NOT NULL,
 CONSTRAINT PK_PortalCuratorAssignments PRIMARY KEY(EmployeeCode,Subdivision));
 DELETE FROM {TABLE};''')
    rows=CuratorEmployee.subdivisions.through.objects.filter(curatoremployee__is_archived=False).values_list('curatoremployee__employee_code','subdivision__subdivision_key','curatoremployee__full_name')
    values=list(rows)
    if values:cursor.executemany(f'INSERT INTO {TABLE} VALUES(?,?,?)',values)


def pair_batch(definition,variant='all_2026',*,original_rules=False,group_table='motivation.PortalPlanningGroupRules'):
    source=strip(definition)
    if 'Директора' not in source or not re.search(r'Employee(?:Name|Code)',source,re.I):raise ValidationError('Нужна SQL-процедура с персональным расчётом директоров для подключения кураторов.')
    if variant=='main':
        source=isolate(source,'All2026').replace('PortalCalcBySub','PortalCalcAll2026')
    elif variant=='separate_2026':
        # Curators always retain their physical branch and its own rates.
        source=source.replace('Separate2026','All2026').replace('PortalSeparateSubdivisions','PortalCalculationSubdivisions')
        # Undo the transition-only Moscow rate lookup for the curator's own rates.
        source=re.sub(r"COALESCE\(\(SELECT TOP \(1\) m.TargetName FROM DataWH.motivation.PortalSubdivisionMerge m WHERE m.SourceName=(\w+\.SubdivisionName)\),\1\)",r'\1',source,flags=re.I)
    batch=private_sql(source,'combined',uuid4(),original_rules=original_rules,group_table=group_table)
    batch=batch.replace(ALLOCATION_SQL,'SELECT * INTO #share_transformed FROM #portal_motivation_sales;\n')
    batch=batch.replace("SELECT * INTO #share_subdivisions FROM dbo.Subdivisions WHERE SubdivisionName IN(N'Д-Москва',N'ТЕРМОТРЕЙД');",
        'SELECT s.* INTO #share_subdivisions FROM dbo.Subdivisions s JOIN #curator_request r ON r.Subdivision=s.SubdivisionName;')
    batch=batch.replace("UPDATE #share_subdivisions SET SubdivisionName=N'Д-Москва' WHERE SubdivisionName=N'ТЕРМОТРЕЙД';",'')
    batch=re.sub(r'(?:(?:\[?DataWH\]?\s*\.\s*)?\[?motivation\]?\s*\.\s*)\[?vSubdivisionMotiveCoeffTo\]?(?!\w)','#curator_coeff',batch,flags=re.I)
    batch=batch.replace('SELECT @PortalPreparedRun AS SharePreparedRun;','')
    batch=re.sub(r'\bIF\s+@@TRANCOUNT\s*<>\s*0\s+THROW[^;]+;', '', batch, flags=re.I)
    batch=re.sub(r'(?:(?:\[?DataWH\]?\s*\.\s*)?\[?motivation\]?\s*\.\s*)\[?SubdivisionMotiveCoeff\]?(?!\w)','#curator_raw_coeff',batch,flags=re.I)
    batch=re.sub(r'COALESCE\(\(SELECT m.TargetName FROM motivation.PortalSubdivisionMerge m WHERE m.SourceName=(\w+\.SubdivisionName)\),\1\)',r'\1',batch,flags=re.I)
    prefix='''
IF OBJECT_ID('tempdb..#curator_raw_coeff') IS NOT NULL DROP TABLE #curator_raw_coeff;
SELECT sc.* INTO #curator_raw_coeff FROM DataWH.motivation.SubdivisionMotiveCoeff sc
WHERE LTRIM(RTRIM(sc.TypeCoeff)) NOT IN(N'Директора',N'Кураторы') OR
 (LTRIM(RTRIM(sc.TypeCoeff))=N'Кураторы' AND EXISTS(SELECT 1 FROM #curator_request r WHERE r.EmployeeCode=sc.EmployeeCode AND r.Subdivision=sc.Subdivision));
UPDATE #curator_raw_coeff SET TypeCoeff=N'Директора' WHERE LTRIM(RTRIM(TypeCoeff))=N'Кураторы';
IF OBJECT_ID('tempdb..#curator_coeff') IS NOT NULL DROP TABLE #curator_coeff;
SELECT sc.* INTO #curator_coeff FROM DataWH.motivation.vSubdivisionMotiveCoeffTo sc
WHERE LTRIM(RTRIM(sc.TypeCoeff)) NOT IN(N'Директора',N'Кураторы') OR
 (LTRIM(RTRIM(sc.TypeCoeff))=N'Кураторы' AND EXISTS(SELECT 1 FROM #curator_request r
 WHERE r.EmployeeCode=sc.EmployeeCode AND r.Subdivision=sc.Subdivision));
IF EXISTS(SELECT 1 FROM #curator_request r WHERE NOT EXISTS(SELECT 1 FROM #curator_coeff sc WHERE LTRIM(RTRIM(sc.TypeCoeff))=N'Кураторы' AND sc.EmployeeCode=r.EmployeeCode AND sc.Subdivision=r.Subdivision))
 THROW 50081,N'Для куратора и подразделения нет выгруженных ставок. Проверьте версию и выгрузку коэффициентов.',1;
UPDATE #curator_coeff SET TypeCoeff=N'Директора' WHERE LTRIM(RTRIM(TypeCoeff))=N'Кураторы';
'''
    # Drop only temp sets owned by this calculation between successive pairs.
    from .shareholder_sql import temporary_tables
    cleanup='\n'.join("IF OBJECT_ID('tempdb.."+name+"') IS NOT NULL DROP TABLE "+name+';' for name in temporary_tables(batch) if name not in ('#share_parameters','#curator_request','#curator_coeff'))
    suffix=''
    for table in ('#share_report','#share_annual','#share_fact',*(['#share_eligibility'] if original_rules else [])):
        suffix+=f"\nDELETE FROM {table} WHERE LTRIM(RTRIM([ТипКоэф]))<>N'Директора' OR NOT EXISTS(SELECT 1 FROM #curator_request r WHERE LTRIM(RTRIM(r.FullName))=LTRIM(RTRIM([АвторСделки])));\nUPDATE {table} SET [ТипКоэф]=N'Кураторы';"
    # Reusing native SQL must not overwrite the parent Agent stage timings.
    body=prefix+cleanup+'\n'+batch+'\n'+suffix
    return ("DECLARE @CuratorSavedProfile sql_variant=SESSION_CONTEXT(N'PortalProfileRun');\n"
        "EXEC sys.sp_set_session_context @key=N'PortalProfileRun',@value=NULL;\nBEGIN TRY\n"+body+"\nEND TRY BEGIN CATCH\n"
        "EXEC sys.sp_set_session_context @key=N'PortalProfileRun',@value=@CuratorSavedProfile;\nTHROW;\nEND CATCH;\n"
        "EXEC sys.sp_set_session_context @key=N'PortalProfileRun',@value=@CuratorSavedProfile;")


def append_to_procedure(definition,variant):
    """Install inside the portal copy only, after ordinary native results finish."""
    from .curators import enabled
    if variant=='test_2025' or not enabled():return definition
    if START in definition:raise ValidationError('Кураторы уже подключены к процедуре.')
    if "Директора" not in definition or not re.search(r'Employee(?:Name|Code)',definition,re.I):
        raise ValidationError('Исходная SQL-процедура не содержит персонального расчёта директоров. Для подключения кураторов нужна совместимая версия процедуры.')
    batch=pair_batch(definition,variant)
    suffix={'main':'','all_2026':'All2026','separate_2026':'Separate2026'}[variant]
    for source,target in (('#share_report','ReportMotivationByDealAuthor'),('#share_annual','AnnualAccrual'),('#share_fact','Fact')):
        batch+=f'\nINSERT INTO DataWH.motivation.{target}{suffix} SELECT * FROM {source};'
    # Preserve one unreplicated sales base for each physical branch, regardless
    # of how many curators are assigned there. Curator pay remains per person.
    batch+=f'''
IF NOT EXISTS(SELECT 1 FROM DataWH.motivation.PlanFact{suffix} p JOIN #curator_request r ON p.[Подразделение]=r.Subdivision WHERE p.[ТипКоэф]=N'Кураторы')
BEGIN
 DELETE FROM #share_plan_fact WHERE LTRIM(RTRIM([ТипКоэф]))<>N'Продажи';
 UPDATE #share_plan_fact SET [ТипКоэф]=N'Кураторы';
 INSERT INTO DataWH.motivation.PlanFact{suffix} SELECT * FROM #share_plan_fact;
END;'''
    code=f'''
{START}
IF OBJECT_ID(N'{TABLE}',N'U') IS NOT NULL AND EXISTS(SELECT 1 FROM {TABLE})
BEGIN
 CREATE TABLE #share_parameters(FactYear int,PlanVersion nvarchar(500),QuarterShare decimal(9,8),YearShare decimal(9,8),PayoutThreshold decimal(9,8),Cutoff date);
 INSERT INTO #share_parameters VALUES(@FactYear,@PlanVersion,@QuarterShare,@YearShare,@PayoutThreshold,DATEFROMPARTS(@FactYear+1,1,1));
 CREATE TABLE #curator_request(EmployeeCode nvarchar(64) COLLATE DATABASE_DEFAULT,Subdivision nvarchar(255) COLLATE DATABASE_DEFAULT,FullName nvarchar(255) COLLATE DATABASE_DEFAULT);
 DECLARE @CuratorBranch nvarchar(255),@CuratorBatch nvarchar(max)=N'{batch.replace("'","''")}';
 DECLARE CuratorPairs CURSOR LOCAL FAST_FORWARD FOR SELECT DISTINCT Subdivision FROM {TABLE};
 OPEN CuratorPairs;
 BEGIN TRY
 BEGIN TRANSACTION;
 FETCH NEXT FROM CuratorPairs INTO @CuratorBranch;
 WHILE @@FETCH_STATUS=0
 BEGIN
  DELETE FROM #curator_request;
  INSERT INTO #curator_request SELECT EmployeeCode,Subdivision,FullName FROM {TABLE} WHERE Subdivision=@CuratorBranch;
  EXEC sys.sp_executesql @CuratorBatch;
  FETCH NEXT FROM CuratorPairs INTO @CuratorBranch;
 END;
 COMMIT TRANSACTION;
 CLOSE CuratorPairs;DEALLOCATE CuratorPairs;
 END TRY BEGIN CATCH
 IF XACT_STATE()<>0 ROLLBACK TRANSACTION;
 CLOSE CuratorPairs;DEALLOCATE CuratorPairs;
 THROW;
 END CATCH;
 DROP TABLE #curator_request;DROP TABLE #share_parameters;
END;
{END}
'''
    # Native procedure normally commits its own transaction before this point.
    mask=__import__('admin_motivation.services.pilot_formula',fromlist=['mask_sql']).mask_sql(definition)
    header=re.search(r'\bPROC(?:EDURE)?\s+[\s\S]*?\bAS\b',mask,re.I)
    if not header:raise ValidationError('Не распознано тело расчётной процедуры.')
    outer_begin=re.match(r'\s*BEGIN\b(?!\s+(?:TRY|TRAN|TRANSACTION)\b)',mask[header.end():],re.I)
    if not outer_begin:return definition+'\n'+code
    endings=list(re.finditer(r'\bEND\b',mask,re.I))
    if not endings or re.match(r'\s*(?:TRY|CATCH)\b',mask[endings[-1].end():],re.I):raise ValidationError('Не распознан конец расчётной процедуры.')
    at=endings[-1].start()
    return definition[:at]+code+definition[at:]



def shareholder_snapshots(run,cursor,definition,save_query):
    """Same source cutoff, one independent director calculation per curator pair."""
    from .curators import enabled
    if not enabled():return 0
    from .shareholder_regions import enabled as regional_enabled,BRANCH_SQL
    scope=BRANCH_SQL if regional_enabled(run) else "N'Д-Москва',N'ТЕРМОТРЕЙД'"
    cursor.execute(f'''IF OBJECT_ID(N'{TABLE}',N'U') IS NOT NULL
 SELECT EmployeeCode,Subdivision,FullName FROM {TABLE} WHERE Subdivision IN({scope})
 ELSE SELECT CAST(NULL AS nvarchar(64)),CAST(NULL AS nvarchar(255)),CAST(NULL AS nvarchar(255)) WHERE 1=0;''')
    pairs=cursor.fetchall()
    if not pairs:return 0
    from .shareholder_original_rules import enabled,performance_sql as original_performance_sql
    batch=pair_batch(definition,original_rules=enabled(run),group_table='#share_group_rules')
    cursor.execute('CREATE TABLE #curator_request(EmployeeCode nvarchar(64) COLLATE DATABASE_DEFAULT,Subdivision nvarchar(255) COLLATE DATABASE_DEFAULT,FullName nvarchar(255) COLLATE DATABASE_DEFAULT);')
    from .manager_detail import grouped_sql
    from .motivation_performance import grouped_sql as performance_sql,department_sql
    total=0
    branches=sorted({row[1] for row in pairs})
    for branch in branches:
        cursor.execute('DELETE FROM #curator_request')
        cursor.executemany('INSERT INTO #curator_request VALUES(?,?,?)',[row for row in pairs if row[1]==branch])
        cursor.execute(batch)
        while cursor.nextset():pass
        source="SELECT * FROM #share_report WHERE [ТипКоэф]=N'Кураторы'"
        report_count=save_query(run,cursor,'combined','report_cur',grouped_sql(source),kind='Кураторы')
        total+=report_count
        perf=performance_sql(source,coefficient_type='Директора').replace('DataWH.motivation.PlanFactAll2026','#share_plan_fact')
        dept=department_sql(source).replace('DataWH.motivation.PlanFactAll2026','#share_plan_fact')
        if enabled(run):
            perf=original_performance_sql(perf);dept=original_performance_sql(dept)
            eligibility_count=save_query(run,cursor,'combined','eligibility_cur','SELECT * FROM #share_eligibility')
            if report_count and not eligibility_count:raise ValidationError('Не сохранено годовое условие выплаты куратора. Результаты не опубликованы.')
        save_query(run,cursor,'combined','perf_cur',perf,kind='Кураторы')
        save_query(run,cursor,'combined','dept_cur',dept,kind='Кураторы')
        save_query(run,cursor,'combined','annual_cur','SELECT * FROM #share_annual')
    cursor.execute('DROP TABLE #curator_request')
    return total
