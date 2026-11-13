"""Run the native 2025 calculation in session-local temporary tables only."""
import re
from decimal import Decimal
from xml.etree.ElementTree import Element,SubElement,tostring
from django.core.exceptions import ValidationError
from .sales_plans import number
from .coefficient_iteration_calculation import branch,empty_row,ZERO,motivation_plan_rows


def scoped_setup():
    from .recalculation_2025_sql import SETUP
    sql=SETUP.replace('decimal(12,6)','decimal(20,18)').replace('SELECT r.record.value',"SELECT r.record.value('(subdivision/text())[1]','nvarchar(255)') AS Subdivision,\n r.record.value",1)
    sql=sql.replace('GROUP BY PGSales,Group_,Brand,TypeCoeff','GROUP BY Subdivision,PGSales,Group_,Brand,TypeCoeff')
    sql=re.sub(r'SELECT rates\.\*,sub\.SubdivisionName AS Subdivision\s+INTO #test2025_coeff FROM #test2025_rates rates\s+CROSS JOIN \(SELECT DISTINCT SubdivisionName FROM dbo.Subdivisions\) sub;',
        'SELECT * INTO #test2025_coeff FROM #test2025_rates;',sql)
    sql=sql.replace('ON #test2025_coeff(PGSales,TypeCoeff)','ON #test2025_coeff(Subdivision,TypeCoeff) INCLUDE(PGSales,Group_,Brand,K0,K1,K2,K3,K4,DateFrom,DateTo)')
    sql=sql.replace('SELECT r.PGSales,r.TypeCoeff,MIN(v.Coeff_)','SELECT r.Subdivision,r.PGSales,r.TypeCoeff,MIN(v.Coeff_)')
    sql=sql.replace('CREATE UNIQUE INDEX IX_test2025_group_min ON #test2025_group_min(PGSales,TypeCoeff);','')
    sql=sql.replace('GROUP BY r.PGSales,r.TypeCoeff','GROUP BY r.Subdivision,r.PGSales,r.TypeCoeff')
    sql=sql.replace('SELECT TypeCoeff,MIN(Minimum_)','SELECT Subdivision,TypeCoeff,MIN(Minimum_)').replace('GROUP BY TypeCoeff;','GROUP BY Subdivision,TypeCoeff;')
    if 'CROSS JOIN' in sql:raise ValidationError('Не удалось ограничить коэффициенты подразделениями.')
    return sql+'''\nCREATE INDEX IX_iteration_group_rate ON #test2025_group_min(Subdivision,TypeCoeff) INCLUDE(PGSales,Minimum_);
CREATE UNIQUE INDEX IX_iteration_table_rate ON #test2025_table_min(Subdivision,TypeCoeff);
'''


def build_sql(cursor=None):
    from .recalculation_2025_sql import template_source,patch_source,SETUP,FORMULA_SNAPSHOT
    from .formula_defaults import patch_defaults
    from .recalculation_2025_share_cache import patch as shares
    from .payout_threshold import parameterize
    from .first_quarter_inputs import patch as first_quarter
    from .pilot_formula import patch_calculation,mask_sql
    from .calculation_sales_isolation import patch as private_sales
    from .prepared_sales import patch as prepared
    source=patch_source(patch_defaults(template_source())[0])
    source=source.replace(SETUP,scoped_setup(),1)
    named_formulas=FORMULA_SNAPSHOT.replace('FROM DataWH.motivation.vSubdivisionMotiveCoeffTo old',
        'FROM DataWH.motivation.vSubdivisionMotiveCoeffTo old\nINNER JOIN #iteration_names named ON CONVERT(nvarchar(255),old.Subdivision)=named.SubdivisionKey').replace(
        'old.Subdivision=c.Subdivision','named.SubdivisionName=c.Subdivision')
    if source.count(FORMULA_SNAPSHOT)!=1:raise ValidationError('Не распознан снимок исторических формул.')
    source=source.replace(FORMULA_SNAPSHOT,named_formulas,1)

    # Restrict both fallback levels to the SAME uploaded subdivision.
    pattern=r"LEFT JOIN #test2025_group_min gm ON gm.PGSales=(p|t)\.PlanningGroupSalesERP AND gm.TypeCoeff=N'(Политики|Продажи)'\s+LEFT JOIN #test2025_table_min tm ON tm.TypeCoeff=N'\2'"
    def fallback(match):
        alias='sub' if match[1]=='p' else 's'
        return (f"LEFT JOIN #test2025_group_min gm ON gm.PGSales={match[1]}.PlanningGroupSalesERP AND gm.TypeCoeff=N'{match[2]}' AND gm.Subdivision={alias}.SubdivisionName\n"
                f" LEFT JOIN #test2025_table_min tm ON tm.TypeCoeff=N'{match[2]}' AND tm.Subdivision={alias}.SubdivisionName")
    source,count=re.subn(pattern,fallback,source)
    if count!=4:raise ValidationError('Не распознаны четыре ограниченных подстановки ставок 2025.')
    # Use the exact saved portal plan, including ERP imports. No second read of
    # planning.PlanSales or source version with potentially different contents.
    pattern=r'FROM\s*\(\s*SELECT\s+Subdivision\s*,PlanningGroupSalesERP[\s\S]*?\)\s+t\s+INNER JOIN DataWH\.dbo\.Subdivisions s\s+ON t\.Subdivision = s\.SubdivisionKey'
    source,count=re.subn(pattern,'FROM #iteration_plan t\nINNER JOIN DataWH.dbo.Subdivisions s ON t.Subdivision = s.SubdivisionKey',source)
    if count!=2:raise ValidationError('Не распознаны два источника сохранённого плана 2025.')
    source=shares(source);source=first_quarter(parameterize(source));source=patch_calculation(source)
    from .calculation_performance import patch as performance
    source=performance(source)
    source=private_sales(source,cursor=cursor).replace('DataWH.motivation.PortalCalculationSubdivisions','DataWH.dbo.Subdivisions')
    source=source.replace('SELECT SubdivisionKey FROM DataWH.dbo.Subdivisions',"SELECT d.SubdivisionKey FROM DataWH.dbo.Subdivisions d WHERE d.SubdivisionName IN (SELECT n.SubdivisionName FROM #iteration_plan p JOIN #iteration_names n ON n.SubdivisionKey=p.Subdivision)")
    source=prepared(source,historical_author=True,cursor=cursor)
    # The saved importer maps blank planning groups to 9. OTHER. Apply the
    # identical classification to fact so those rows can match the saved plan.
    planning_expression="COALESCE(NULLIF(LTRIM(RTRIM(p.PlanningGroupSalesERP)),N''),N'9. OTHER')"
    source=re.sub(r'\bp\.PlanningGroupSalesERP\b',planning_expression,source,flags=re.I)
    # A computed CTE column no longer inherits the original field name.
    # Name both UNION projections explicitly; predicates keep the expression only.
    projection=r'(?m)^([ \t]*,[ \t]*)'+re.escape(planning_expression)+r'[ \t]*$'
    source,count=re.subn(projection,lambda match:match[1]+planning_expression+' AS PlanningGroupSalesERP',source)
    if count!=2:raise ValidationError('Не распознаны два именованных столбца группы планов продаж в факте 2025.')
    # Physical source joins stay on SubdivisionKey. Every calculation scope,
    # author allocation and payout window uses the business SubdivisionName.
    source=source.replace('s.SubdivisionKey AS PortalSubdivisionKey','s.SubdivisionName AS PortalSubdivisionKey')
    source=source.replace('DENSE_RANK() OVER(ORDER BY s.SubdivisionKey,','DENSE_RANK() OVER(ORDER BY s.SubdivisionName,')
    documents=list(re.finditer(r't\.Subdivision\s+\[ПодразделениеДокумент\]',source))
    if len(documents)!=2:raise ValidationError('Не распознаны области плана 2025 по имени подразделения.')
    for index,match in reversed(list(enumerate(documents))):
        field='t.PortalSubdivisionName' if index==0 else 's.SubdivisionName'
        source=source[:match.start()]+field+' [ПодразделениеДокумент]'+source[match.end():]
    source=source.replace('motivation.PortalFormulaDefaults','#iteration_defaults')
    for old,new in (('FactTest2025','#iteration_fact'),('PlanFactTest2025','#iteration_plan_fact'),
                    ('ReportMotivationByDealAuthorTest2025','#iteration_report'),('AnnualAccrualTest2025','#iteration_annual')):
        full='DataWH.motivation.'+old
        source=source.replace("'"+full+"'","'tempdb.."+new+"'").replace(full,new)
    marker="IF OBJECT_ID('tempdb..#iteration_fact')"
    if source.count(marker)!=1:raise ValidationError('Не распознана граница исходного факта 2025.')
    audit="""ALTER TABLE #fact ALTER COLUMN [ПодразделениеДокумент] nvarchar(255);
UPDATE #fact SET [ПодразделениеДокумент]=[Подразделение];
"""
    source=source.replace(marker,audit+marker,1)
    source=re.sub(r"@Resource = N'motivation\.sp_CalcBySub'","@Resource = @IterationResource",source)
    # Parameter declarations replace the procedure header; no CREATE/ALTER PROC
    # and no persistent output object exists at any point in this calculation.
    header=re.search(r'\bCREATE PROCEDURE motivation\.PortalCalcTest2025[\s\S]*?\bAS\s*(?=BEGIN\b)',source,re.I)
    if not header:raise ValidationError('Не распознан заголовок изолированного расчёта.')
    declarations='''DECLARE @FactYear int=2025,@PlanVersion nvarchar(500),
 @QuarterShare decimal(9,8),@YearShare decimal(9,8),
 @PayoutThreshold decimal(9,8),@Test2025Coefficients nvarchar(max),
 @IterationResource nvarchar(255)=CONCAT(N'PortalCoefficientIteration2025:',@@SPID);
SELECT @PlanVersion=PlanVersion,@QuarterShare=QuarterShare,@YearShare=YearShare,
 @PayoutThreshold=PayoutThreshold,@Test2025Coefficients=Coefficients
FROM #iteration_parameters;
'''
    source=source[:header.start()]+declarations+source[header.end():]
    code=mask_sql(source)
    if 'DataWH.planning.PlanSales' in source or re.search(r'\b(?:CREATE|ALTER)\s+PROC|\b(?:INTO|UPDATE|DELETE\s+FROM|DROP\s+TABLE)\s+(?:DataWH|motivation)\.',code,re.I):
        raise ValidationError('В изолированном расчёте обнаружена запись вне временных таблиц.')
    return source+"\nSELECT CONVERT(nvarchar(36),@PortalPreparedRun) AS prepared_run_id;\n"


def xml(records):
    root=Element('coefficients')
    for record in records:
        child=SubElement(root,'coefficient')
        for name,value in record.items():SubElement(child,name).text=str(value)
    return tostring(root,encoding='unicode')


def read_2025(cursor,plan,rates,settings,progress):
    named_plan=motivation_plan_rows(plan,rates)
    branches=sorted({row['branch'] for row in named_plan})
    # Actuals may contain eligible groups/channels absent from the plan.
    # Supply all available raw classifications; SQL eligibility guards decide
    # whether a missing rate belongs to a genuinely participating row.
    records=rates.coefficients_2025(branches,allow_missing_kinds=True)
    progress('2025: проверка подготовленного факта и временного расчёта',5)
    cursor.execute("DECLARE @l int; EXEC @l=sys.sp_getapplock @Resource=N'PortalMotivationCalculation',@LockMode=N'Shared',@LockOwner=N'Session',@LockTimeout=0; SELECT @l;")
    acquired=cursor.fetchone()
    if not acquired or acquired[0]<0:raise ValidationError('Сейчас выполняется подготовка факта, экспорт или основной расчёт. Повторите сценарий 2025 после завершения.')
    cursor.execute("SELECT OBJECT_ID(N'motivation.PortalPreparedSalesActive'),OBJECT_ID(N'motivation.PortalPreparedSalesRun'),OBJECT_ID(N'motivation.PortalPreparedSales'),OBJECT_ID(N'motivation.PortalFormulaDefaults')")
    objects=cursor.fetchone()
    if not objects or not all(objects[:3]):raise ValidationError('В Центре расчётов сначала подготовьте факт продаж с 2025 года.')
    defaults=[]
    if objects[3]:
        cursor.execute("SELECT Date_,DefaultFormula FROM motivation.PortalFormulaDefaults WHERE Date_<'20260101' ORDER BY Date_")
        defaults=[{'date':str(r[0]),'formula':r[1]} for r in cursor.fetchall()]
    source=build_sql(cursor)
    cursor.execute('SET QUOTED_IDENTIFIER ON; SET ANSI_NULLS ON;')
    cursor.execute('''CREATE TABLE #iteration_plan(Subdivision nvarchar(255) COLLATE DATABASE_DEFAULT,PlanningGroupSalesERP nvarchar(255) COLLATE DATABASE_DEFAULT,
 Date_ date,GroupERP nvarchar(255) COLLATE DATABASE_DEFAULT,[Марка(Бренд)] nvarchar(255) COLLATE DATABASE_DEFAULT,AmountUSD decimal(24,6));
 CREATE TABLE #iteration_defaults(Date_ date NOT NULL PRIMARY KEY,DefaultFormula nvarchar(64) COLLATE DATABASE_DEFAULT);
 CREATE TABLE #iteration_parameters(PlanVersion nvarchar(500),QuarterShare decimal(9,8),YearShare decimal(9,8),
 PayoutThreshold decimal(9,8),Coefficients nvarchar(max));
 SELECT CONVERT(nvarchar(255),SubdivisionKey) AS SubdivisionKey,SubdivisionName
 INTO #iteration_names FROM dbo.Subdivisions;''')
    cursor.executemany('INSERT INTO #iteration_plan VALUES (?,?,?,?,?,?)',
        [(row['branch'],row['planning'],row['date'],
          row['goods'] if row['planning'].strip().upper() in ('1. RAC','2. VRF','3. PROF') else '',
          '' if row['brand']=='*' else row['brand'],str(row['amount'])) for row in plan])
    if defaults:cursor.executemany('INSERT INTO #iteration_defaults VALUES (?,?)',[(r['date'],r['formula'] or None) for r in defaults])
    cursor.execute('''SELECT SubdivisionKey FROM dbo.Subdivisions WHERE SubdivisionKey IN
 (SELECT DISTINCT Subdivision FROM #iteration_plan) GROUP BY SubdivisionKey HAVING COUNT(*)>1''')
    duplicates=cursor.fetchall()
    if duplicates:raise ValidationError('В DWH повторяются SubdivisionKey: '+', '.join(str(row[0]) for row in duplicates))
    cursor.execute('''SELECT DISTINCT p.Subdivision FROM #iteration_plan p LEFT JOIN dbo.Subdivisions s
 ON s.SubdivisionKey=p.Subdivision WHERE s.SubdivisionKey IS NULL''')
    missing=cursor.fetchall()
    if missing:raise ValidationError('В DWH нет подразделений: '+', '.join(str(r[0]) for r in missing))
    progress('2025: факт, распределение плана по авторам и квартальные/годовые выплаты',6)
    # ODBC parameterized execution uses a nested RPC scope. SELECT INTO temp
    # outputs created there disappear when it returns. Bind only INSERT values
    # into a caller-created table; run the native batch without RPC parameters.
    cursor.execute('INSERT INTO #iteration_parameters VALUES (?,?,?,?,?)',
                   'Сохранённый план итерации',str(number(settings['quarter_share'])/100),
                   str(number(settings['year_share'])/100),str(settings['payout_threshold']),xml(records))
    cursor.execute(source)
    # Drain informational result sets emitted by the native SQL batch.
    used_run=None
    while True:
        if cursor.description and str(cursor.description[0][0]).casefold()=='prepared_run_id':used_run=cursor.fetchone()[0]
        if not cursor.nextset():break
    if not used_run:raise ValidationError('Расчёт не подтвердил использованный набор подготовленного факта.')
    cursor.execute('''SELECT LTRIM(RTRIM([ТипКоэф])),[Подразделение],[АвторСделки],[КварталМотивации],
 SUM(COALESCE([СуммаМотивацииUSDУчетПроцента],0)),SUM(COALESCE([Остаток],0)),
 SUM(COALESCE([СуммаМотивацииUSD],0)) FROM #iteration_report WHERE [Год]=2025
 GROUP BY [ТипКоэф],[Подразделение],[АвторСделки],[КварталМотивации]''')
    grouped={}
    for kind,sub,author,period,paid,bonus,possible in cursor.fetchall():
        if kind not in ('Политики','Продажи'):continue
        sub=branch(sub);author=str(author or '') if kind=='Политики' else ''
        row=grouped.setdefault((sub,kind,author),empty_row('actual_2025',2025,sub,kind,author))
        if str(period).strip().casefold()=='год':row['annual_paid']+=number(paid or 0)
        else:
            q=int(str(period).strip()[0]);row['quarters'][q-1]+=number(paid or 0);row['possible'][q-1]+=number(possible or 0)
        row['bonus']+=number(bonus or 0)
    from .recalculation_2025_plan import ELIGIBLE
    from .subdivision_scope import resolve_scope_sql
    cursor.execute('''SELECT LTRIM(RTRIM([ТипКоэф])),[Подразделение],[АвторСделки],[Квартал],
 SUM(CASE WHEN [СуммаМотивацииUSD]>0 THEN [СуммаМотивацииUSD] ELSE 0 END)
 FROM #iteration_fact WHERE [Год]=2025 AND '''+resolve_scope_sql(ELIGIBLE)+''' GROUP BY [ТипКоэф],[Подразделение],[АвторСделки],[Квартал]''')
    for kind,sub,author,q,potential in cursor.fetchall():
        author=str(author or '') if kind=='Политики' else '';identity=(branch(sub),kind,author)
        if identity in grouped:grouped[identity]['annual_quarters'][int(q)-1]+=number(potential or 0)*number(settings['year_share'])/100
    for row in grouped.values():
        row['annual_accrued']=sum(row['annual_quarters'],ZERO)
        row['total']=sum(row['quarters'],ZERO)+row['annual_paid']+row['bonus']
    if not grouped:raise ValidationError('Расчёт 2025 не вернул результаты. Нулевой результат не подставлен.')
    cursor.execute('''SELECT r.RunId,r.CompletedAt FROM motivation.PortalPreparedSalesRun r WHERE r.RunId=? AND r.State_=N'completed' ''',used_run)
    ready=cursor.fetchone()
    metadata={'prepared_run_id':str(ready[0]) if ready else '', 'completed_at':str(ready[1]) if ready else '', 'policy_formula_defaults':defaults}
    return list(grouped.values()),metadata


def read_details(cursor,threshold):
    """Reuse the existing explanation of the SAME native payout/gate rows."""
    from .recalculation_2025_performance import DETAIL_SQL,gates_sql,explain
    cursor.execute(DETAIL_SQL.replace('motivation.ReportMotivationByDealAuthorTest2025','#iteration_report'))
    detail=cursor.fetchall()
    cursor.execute(gates_sql().replace('motivation.PlanFactTest2025','#iteration_plan_fact'))
    rows=explain(detail,cursor.fetchall(),threshold)
    cursor.execute('SELECT PGSales,Group_,Brand,TypeCoeff,Subdivision,DateFrom,DateTo,VariationCalculate FROM #test2025_formulas')
    formulas=[list(r) for r in cursor.fetchall()]
    return [{**r,'year':2025,'scenario':'actual_2025','employee':r['author'],'note':r['reason']} for r in rows],formulas
