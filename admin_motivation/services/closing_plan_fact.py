"""Department eligibility plan/fact, counted once rather than per employee."""
from decimal import Decimal
from django.core.cache import cache
from django.core.exceptions import ValidationError


def load(execution, period):
    from . import reports_next
    from .reports import load_report
    variant=execution.parameters.get('variant','main')
    key=f'closing-plan-fact-v1:{execution.pk}:{execution.completed_at}:{period}'
    with reports_next.current_run(execution):
        cached=cache.get(key)
        if cached is not None:return cached
        data=load_report('manager-detail',{'TypeCoeff_':'Продажи','Subdivision':[],'DealAuthor':[],
            'gp_sales':[],'GPGoods':[],'Brend':[],'Years':[str(execution.parameters['fact_year'])],'Currency':'USD'},
            premium_period=[str(period)],summary_only=True,include_performance=True,calculation_variant=variant)
        result={}
        for row in data.get('department_performance',[]):
            if reports_next.period_code(row['КварталМотивации'])!=str(period):continue
            if str(row['Год'])!=str(execution.parameters['fact_year']):continue
            branch=row['Подразделение'].casefold()
            values={name:Decimal(str(row[field])) if row.get(field) is not None else None for name,field in [('plan','Plan_'),('fact','Fact_')]}
            if branch in result and result[branch]!=values:
                raise ValidationError('Неоднозначный план-факт подразделения.')
            result[branch]=values
        cache.set(key,result,600)
        return result


def shares(comparison, values):
    fact=values.get('fact')
    return {name:Decimal(str(comparison[field]))/fact*100 if fact is not None and fact!=0 else None
            for name,field in [('before_fact_percent','before'),('after_fact_percent','after')]}
