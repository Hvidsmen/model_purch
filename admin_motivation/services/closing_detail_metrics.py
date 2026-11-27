"""USD plan/fact at department, motivation kind and physical author scope."""
from decimal import Decimal
from django.core.cache import cache
from .closing_details import identity
from .closing_plan_fact import load as department, shares


def load(execution, period, branch):
    from .reports_next import current_run
    from .motivation_performance import plan_fact_table, period_coverage
    from ..conns import connect_database
    key=f'closing-detail-metrics-v1:{execution.pk}:{execution.completed_at}:{period}:{branch}'
    with current_run(execution):
        cached=cache.get(key)
        if cached is not None:return cached
        connection,cursor=connect_database('vm-dwh','DataWH')
        try:
            connection.timeout=30
            label='Год' if period==5 else f'{period} Квартал'
            cursor.execute('WITH c AS (SELECT CAST(? AS nvarchar(30)) AS [КварталМотивации]) '
                'SELECT LTRIM(RTRIM(pf.[ТипКоэф])),pf.[АвторСделки],SUM(pf.[СуммаПланUSD]),SUM(pf.[СуммаОтгрузокUSD]) '
                'FROM '+plan_fact_table(execution.parameters.get('variant','main'))+' pf CROSS JOIN c '
                "WHERE pf.[Подразделение]=? AND pf.[Год]=? AND LTRIM(RTRIM(pf.[ТипКоэф])) IN (N'Продажи',N'Политики') AND ("+period_coverage('c')+') '
                'GROUP BY LTRIM(RTRIM(pf.[ТипКоэф])),pf.[АвторСделки]',label,branch,execution.parameters['fact_year'])
            inputs=list(cursor.fetchall())
        finally:
            connection.close()
        kinds={};people={}
        for kind,author,plan,fact in inputs:
            values={key:Decimal(str(value)) if value is not None else None for key,value in [('plan',plan),('fact',fact)]}
            previous=people.setdefault(kind,{}).get(identity(author))
            people[kind][identity(author)]={name:previous[name]+values[name] if previous[name] is not None and values[name] is not None else None for name in ('plan','fact')} if previous else values
            bucket=kinds.setdefault(kind,{'plan':Decimal(0),'fact':Decimal(0)})
            for name in ('plan','fact'):
                bucket[name]=bucket[name]+values[name] if bucket[name] is not None and values[name] is not None else None
        # Directors share the unreplicated departmental sales base.
        kinds['Директора']=dict(kinds.get('Продажи',{}))
        result={'global':department(execution,period).get(branch.casefold(),{}),'kinds':kinds,'people':people}
        cache.set(key,result,600)
        return result


def enrich(comparison, usd, metrics):
    def apply(target, numerator, values, scope):
        percentages=shares(numerator,values) if numerator is not None else {'before_fact_percent':None,'after_fact_percent':None}
        target.update(plan=values.get('plan'),fact=values.get('fact'),metric_scope=scope,**percentages)
    apply(comparison,usd,metrics.get('global',{}),'Всё подразделение')
    usd_kinds={row['kind']:row for row in usd['kinds']}
    usd_payments={row['payment']:row for row in usd['payments']}
    usd_people={(row['kind'],identity(row['author']),row['payment']):row for row in usd['rows']}
    for row in comparison['kinds']:
        apply(row,usd_kinds.get(row['kind']),metrics.get('kinds',{}).get(row['kind'],{}),'База вида мотивации')
    for row in comparison['payments']:
        apply(row,usd_payments.get(row['payment']),metrics.get('global',{}),'Всё подразделение')
    for row in comparison['rows']:
        values=metrics.get('people',{}).get(row['kind'],{}).get(identity(row['author']),{})
        scope='Личный план-факт'
        if identity(row['author']) in ('','—','-') or row.get('author_status')=='unallocated':
            values={};scope='Сотрудник не указан'
        if row['kind']=='Директора':
            values=metrics.get('kinds',{}).get('Директора',{});scope='База директоров подразделения'
        apply(row,usd_people.get((row['kind'],identity(row['author']),row['payment'])),values,scope)
    return comparison
