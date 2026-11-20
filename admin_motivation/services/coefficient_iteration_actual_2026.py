"""Actual closed quarters, using the isolated native payout and gate calculation."""
from copy import deepcopy
import re
from django.core.exceptions import ValidationError
from .coefficient_iteration_calculation import MOSCOW,TT,ZERO,key


def sql_2026(sql):
    # Change year literals, never object names (the native private tables retain
    # their identifiers). Both the fact and saved plan end on September 30.
    sql=re.sub(r'(?<![\w-])(?:20250101|20251231|20260101|2025)(?![\w-])',
        lambda m:{'20250101':'20260101','20251231':'20261231','20260101':'20270101','2025':'2026'}[m[0]],sql)
    sql=sql.replace('DECLARE @DateTo date = DATEFROMPARTS(@FactYear + 1, 1, 1);',
                    "DECLARE @DateTo date = CONVERT(date,'20261001');")
    sql=sql.replace('ON CONVERT(nvarchar(255),old.Subdivision)=named.SubdivisionKey',
        'ON CONVERT(nvarchar(255),old.Subdivision)=named.SubdivisionKey AND named.SubdivisionKey IN (SELECT SubdivisionKey FROM #iteration_actual_formula_keys)')
    # The selected approved portal version, never a view mixing revisions.
    sql=sql.replace('DataWH.motivation.vSubdivisionMotiveCoeffTo old','#iteration_actual_policy_formulas old')
    sql=sql.replace("OBJECT_ID(N'motivation.PortalFormulaDefaults')","OBJECT_ID(N'tempdb..#iteration_actual_defaults')")
    sql=sql.replace('FROM motivation.PortalFormulaDefaults','FROM #iteration_actual_defaults')
    return re.sub(r'\b(?:DataWH\.)?dbo\.Subdivisions\b','#iteration_actual_subdivisions',sql,flags=re.I)


def policy_formulas(snapshot,rates):
    if 'coefficients' not in snapshot:raise ValidationError('В запуске нет снимка формул выбранной версии. Создайте новый запуск расчёта.')
    from .motivation_exclusions import kind_name
    rows={};identities={}
    for sub,kind,p,g,b,formula in snapshot['coefficients']:
        kind=kind_name(kind)
        if rates.role(rates.name_for_key(str(sub))) not in (MOSCOW,TT) or kind not in ('Политики','Продажи'):continue
        identity=(rates.role(rates.name_for_key(str(sub))),kind,*key((p,g,b)))
        if identity in identities and identities[identity]!=formula:
            raise ValidationError('В выбранной версии разные формулы одного товарного ключа: '+' · '.join(identity)+'. Исправьте формулы по сегментам.')
        identities[identity]=formula
        rows[(str(sub),*identity[1:])]=(str(sub),kind,p,g,b,formula)
    return list(rows.values())


class Cursor:
    def __init__(self,cursor):self.cursor=cursor
    def __getattr__(self,name):return getattr(self.cursor,name)
    def execute(self,sql,*args):return self.cursor.execute(sql_2026(sql),*args)
    def executemany(self,sql,args):return self.cursor.executemany(sql_2026(sql),args)


class Rates:
    def __init__(self,rates):self.rates=rates
    def __getattr__(self,name):return getattr(self.rates,name)
    def name_for_key(self,value):return self.rates.name_for_key(value)
    def directors_for(self,sub,year):return self.rates.directors_for(sub,2026)
    def coefficients_2025(self,branches,**kwargs):return self.rates.coefficients_2025(branches,**kwargs)


def calculate(cursor,plans,rates,settings,formulas,progress):
    from .coefficient_iteration_2025 import read_2025,read_details
    from .coefficient_iteration_runner import directors_2025
    local_formulas=policy_formulas(formulas,rates)
    selected=[row for row in plans if row['date'].month<=9 and rates.role(row.get('branch_name') or rates.name_for_key(row['branch'])) in (MOSCOW,TT)]
    if {rates.role(r.get('branch_name') or rates.name_for_key(r['branch'])) for r in selected}!={MOSCOW,TT}:
        raise ValidationError('Для I–III кварталов нужны планы Москвы и Термотрейда.')
    names=sorted({name for name in rates.names.values() if rates.role(name) in (MOSCOW,TT)})
    if {rates.role(name) for name in names}!={MOSCOW,TT}:
        raise ValidationError('В снимке справочника не найдены Москва и Термотрейд. Обновите сопоставление подразделений.')
    # SQL Server ODBC executes parameterized batches in a nested RPC scope.
    # A temp table created in that scope disappears when execute returns.
    # Create both tables in the caller session; bind values only in INSERTs.
    cursor.execute('''CREATE TABLE #iteration_actual_subdivisions(
 SubdivisionKey nvarchar(255) COLLATE DATABASE_DEFAULT,
 SubdivisionName nvarchar(255) COLLATE DATABASE_DEFAULT,
 Chanel nvarchar(255) COLLATE DATABASE_DEFAULT);
 CREATE TABLE #iteration_actual_formula_keys(SubdivisionKey nvarchar(255) COLLATE DATABASE_DEFAULT);
 CREATE TABLE #iteration_actual_policy_formulas(Subdivision nvarchar(255) COLLATE DATABASE_DEFAULT,
 TypeCoeff nvarchar(30) COLLATE DATABASE_DEFAULT,PGSales nvarchar(255) COLLATE DATABASE_DEFAULT,
 Group_ nvarchar(255) COLLATE DATABASE_DEFAULT,Brand nvarchar(255) COLLATE DATABASE_DEFAULT,
 VariationCalculate nvarchar(64) COLLATE DATABASE_DEFAULT,DateFrom date,DateTo date);
 CREATE TABLE #iteration_actual_defaults(Date_ date,DefaultFormula nvarchar(64) COLLATE DATABASE_DEFAULT);''')
    if local_formulas:
        cursor.executemany("INSERT INTO #iteration_actual_policy_formulas VALUES (?,?,?,?,?,?,'20260101','20261231')",local_formulas)
    cursor.execute("INSERT INTO #iteration_actual_defaults VALUES ('20260101',?)",formulas.get('default') or None)
    cursor.execute('''INSERT INTO #iteration_actual_subdivisions(SubdivisionKey,SubdivisionName,Chanel)
 SELECT CONVERT(nvarchar(255),SubdivisionKey),SubdivisionName,Chanel FROM dbo.Subdivisions WHERE SubdivisionName IN ('''+','.join('?' for _ in names)+')',*names)
    cursor.execute('INSERT INTO #iteration_actual_formula_keys(SubdivisionKey) SELECT SubdivisionKey FROM #iteration_actual_subdivisions')
    # Keep physical keys for fact/plan joins and normalize each branch separately.
    cursor.executemany('UPDATE #iteration_actual_subdivisions SET SubdivisionName=? WHERE SubdivisionName=?',
                       [(rates.role(name),name) for name in names])
    private=Cursor(cursor);adapter=Rates(rates)
    selected=[{**row,'branch_name':rates.role(row.get('branch_name') or rates.name_for_key(row['branch']))} for row in selected]
    # USD is the calculation base for 2026. The runner freezes the configured
    # USD/RUB conversion, consistently with the existing 2026 scenario.
    actual_settings={**settings,'currency_rule':''}
    rows,source=read_2025(private,selected,adapter,actual_settings,
        lambda label,step:progress(label.replace('2025','2026 · I–III кварталы'),step))
    details,historical=read_details(private,settings['payout_threshold'])
    directors,director_details=directors_2025(private,selected,adapter,actual_settings,formulas)
    rows.extend(directors);details.extend(director_details)
    source.update({'source':'Москва и Термотрейд раздельно: фактическое выполнение I–III кварталов 2026; годовая часть только начислена. Доли авторов плана — по факту I–III кварталов.',
                   'rule':'iteration-actual-2026-q1-q3-v6-separate-branches','from':'2026-01-01','through':'2026-09-30','historical_formulas':historical})
    return rows,details,source


def quarter_result(rows,details,quarter):
    """A sheet is one quarter's payment, with no annual payout or Q4 carry."""
    result=deepcopy(rows)
    for row in result:
        row.update(year=2026,scenario=f'actual_q{quarter}_2026')
        for field in ('quarters','possible','annual_quarters'):
            row[field]=[value if index==quarter-1 else ZERO for index,value in enumerate(row[field])]
        row['annual_accrued']=sum(row['annual_quarters'],ZERO)
        row['annual_paid']=ZERO;row['bonus']=ZERO;row['total']=sum(row['quarters'],ZERO)
    selected=[]
    for detail in details:
        period=str(detail.get('period',''))
        if period[:1]!=str(quarter):continue
        selected.append({**detail,'year':2026,'scenario':f'actual_q{quarter}_2026'})
    return result,selected
