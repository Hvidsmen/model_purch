"""Actual closed quarters, using the isolated native payout and gate calculation."""
from copy import deepcopy
import re
from django.core.exceptions import ValidationError
from .coefficient_iteration_calculation import MOSCOW,TT,MERGED,ZERO


def sql_2026(sql):
    # Change year literals, never object names (the native private tables retain
    # their identifiers). Both the fact and saved plan end on September 30.
    sql=re.sub(r'(?<![\w-])(?:20250101|20251231|20260101|2025)(?![\w-])',
        lambda m:{'20250101':'20260101','20251231':'20261231','20260101':'20270101','2025':'2026'}[m[0]],sql)
    sql=sql.replace('DECLARE @DateTo date = DATEFROMPARTS(@FactYear + 1, 1, 1);',
                    "DECLARE @DateTo date = CONVERT(date,'20261001');")
    sql=sql.replace('ON CONVERT(nvarchar(255),old.Subdivision)=named.SubdivisionKey',
        'ON CONVERT(nvarchar(255),old.Subdivision)=named.SubdivisionKey AND named.SubdivisionKey IN (SELECT SubdivisionKey FROM #iteration_actual_formula_keys)')
    return re.sub(r'\b(?:DataWH\.)?dbo\.Subdivisions\b','#iteration_actual_subdivisions',sql,flags=re.I)


class Cursor:
    def __init__(self,cursor):self.cursor=cursor
    def __getattr__(self,name):return getattr(self.cursor,name)
    def execute(self,sql,*args):return self.cursor.execute(sql_2026(sql),*args)
    def executemany(self,sql,args):return self.cursor.executemany(sql_2026(sql),args)


class Rates:
    def __init__(self,rates):self.rates=rates
    def __getattr__(self,name):return getattr(self.rates,name)
    def name_for_key(self,value):
        name=self.rates.name_for_key(value)
        return MERGED if self.rates.role(name) in (MOSCOW,TT) else name
    def rate_scope(self,scope,kind):return self.rates.rate_scope(MOSCOW if scope==MERGED else scope,kind)
    def classification(self,scope,kind,dimensions):return self.rates.classification(MOSCOW if scope==MERGED else scope,kind,dimensions)
    def directors_for(self,sub,year):return self.rates.directors_for(sub,2026)
    def coefficients_2025(self,branches,**kwargs):
        records=self.rates.coefficients_2025([MOSCOW],**kwargs)
        return [{**record,'subdivision':MERGED} for record in records]


def calculate(cursor,plans,rates,settings,formulas,progress):
    from .coefficient_iteration_2025 import read_2025,read_details
    from .coefficient_iteration_runner import directors_2025
    selected=[row for row in plans if row['date'].month<=9 and rates.role(row.get('branch_name') or rates.name_for_key(row['branch'])) in (MOSCOW,TT)]
    if {rates.role(r.get('branch_name') or rates.name_for_key(r['branch'])) for r in selected}!={MOSCOW,TT}:
        raise ValidationError('Для I–III кварталов нужны планы Москвы и Термотрейда.')
    names=sorted({name for name in rates.names.values() if rates.role(name) in (MOSCOW,TT)})
    cursor.execute('SELECT * INTO #iteration_actual_subdivisions FROM dbo.Subdivisions WHERE SubdivisionName IN ('+','.join('?' for _ in names)+')',*names)
    moscow=[name for name in names if rates.role(name)==MOSCOW]
    cursor.execute('SELECT SubdivisionKey INTO #iteration_actual_formula_keys FROM #iteration_actual_subdivisions WHERE SubdivisionName IN ('+','.join('?' for _ in moscow)+')',*moscow)
    cursor.execute('UPDATE #iteration_actual_subdivisions SET SubdivisionName=?',MERGED)
    private=Cursor(cursor);adapter=Rates(rates)
    selected=[{**row,'branch_name':MERGED} for row in selected]
    # USD is the calculation base for 2026. The runner freezes the configured
    # USD/RUB conversion, consistently with the existing 2026 scenario.
    actual_settings={**settings,'currency_rule':''}
    rows,source=read_2025(private,selected,adapter,actual_settings,
        lambda label,step:progress(label.replace('2025','2026 · I–III кварталы'),step))
    details,historical=read_details(private,settings['payout_threshold'])
    directors,director_details=directors_2025(private,selected,adapter,actual_settings,formulas)
    rows.extend(directors);details.extend(director_details)
    source.update({'source':'Москва + Термотрейд: фактическое выполнение I–III кварталов 2026; годовая часть только начислена. Доли авторов плана — по факту I–III кварталов.',
                   'rule':'iteration-actual-2026-q1-q3-v1','from':'2026-01-01','through':'2026-09-30','historical_formulas':historical})
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
