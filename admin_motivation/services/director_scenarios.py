"""Five isolated director comparisons; never edit the employee roster or rates."""
from collections import defaultdict
from decimal import Decimal
import json
from types import SimpleNamespace
from django.core.exceptions import ValidationError
from django.db import transaction
from ..models import (SalesPlanScenario, GlobalCoeffVersion, DirectorEmployee,
                      SubdivisionDirectorCoeff, SubdivisionManagerCoeff, DirectorScenarioComparison)
from .director_comparison import DirectorIndex
from .subdivision_aliases import mapping, canonical, coefficient_rows
from .sql_export import director_variation
from .director_scenario_inputs import read_sources, plan_inputs, norm, BRANCHES
from .sales_plans import number

ZERO=Decimal(0)
MERGED='Д-Москва + ТЕРМОТРЕЙД'
FORMULAS=('Подразделение(Глобально)','Подразделение','Группа планов продаж','Группа товаров','Марка(Бренд)')
RULE='director-comparison-five-v4-verified-inputs'


class Rates:
    def __init__(self,version):
        self.version=version;self.aliases=mapping();self.index=DirectorIndex([version.pk],local=True)
        self.formulas={};self.cache={};self.factors={};self.matched_keys={}
        corrections=SubdivisionManagerCoeff.objects.filter(version=version).values_list(
            "subdivision__subdivision_key","kind__name","coeff")
        for sub,kind,value in coefficient_rows(corrections,0,(0,1)):
            code=norm(kind).replace(" ","").replace(".","")
            period={"1кв":1,"2кв":2,"3кв":3,"4кв":4,"год":5,"year":5}.get(code)
            if sub not in {self.rate_branch(b) for b in BRANCHES}:continue
            if period is None:raise ValidationError("Неизвестный период поправки подразделения.")
            if (sub,period) in self.factors:raise ValidationError("Дубли поправок подразделения.")
            self.factors[sub,period]=number(value)
        rows=SubdivisionDirectorCoeff.objects.filter(version=version).values_list(
            'version_id','subdivision__subdivision_key','employee_id','goods__planning_group_sales',
            'goods__group','goods__brand','variation_calculate__variation_name')
        for row in coefficient_rows(rows,1,range(6)):self.formulas[tuple(row[:6])]=row[6]

    def rate_branch(self, branch):
        # Historical inputs use the fixed ТЕРМОТРЕЙД spelling; the model may
        # store Термотрейд. Apply its merge rule before looking up rates.
        source=next((name for name in self.aliases if norm(name)==norm(branch)),branch)
        target=canonical(source,self.aliases)
        names={key[1] for key in self.index.employees}
        matches=[name for name in names if norm(name)==norm(target)]
        if len(matches)>1:
            raise ValidationError('Неоднозначное название подразделения в директорских ставках.')
        return matches[0] if matches else target

    def factor(self,branch,period):
        return self.factors.get((self.rate_branch(branch),period),Decimal(1))

    def get(self,source,branch,row):
        sub=self.rate_branch(branch)
        key=(self.version.pk,sub,source.pk,row['planning'],row['goods'] or '*',row['brand'] or '*')
        if key not in self.cache:
            # Empty classified dimensions are represented as either '' or '*'
            # in imported model goods. Resolve their equivalent exact cells
            # before taking a minimum; conflicting aliases must be explicit.
            groups=(key[4],'') if key[4]=='*' else (key[4],)
            brands=(key[5],'') if key[5]=='*' else (key[5],)
            matches=[(*key[:4],group,brand) for group in groups for brand in brands
                if (*key[:4],group,brand) in self.index.cells]
            if len(matches)>1:
                signatures={(tuple(self.index.cells[item]),self.formulas.get(item)) for item in matches}
                if len(signatures)>1:
                    raise ValidationError(f'Неоднозначные ставки директора: пустое значение и * в «{branch}», {row["planning"]}.')
            matched=matches[0] if matches else key
            self.matched_keys[key]=matched
            values,origin=self.index.coefficients(*matched)
            if origin=='missing':raise ValidationError(f'Нет директорских ставок выбранного источника для «{branch}». Проверьте сотрудника и версию 2026.')
            formula=self.formulas.get(matched) or self.version.fallback_variation or director_variation(SimpleNamespace(planning_group_sales=row['planning']))
            if formula not in FORMULAS:raise ValidationError(f'Формула «{formula}» не поддерживается сравнением директоров.')
            self.cache[key]=(values,formula,origin)
        return self.cache[key]


def scope(formula,row):
    dimensions=(row['planning'],row['goods'],row['brand'])
    count={'Марка(Бренд)':3,'Группа товаров':2,'Группа планов продаж':1}.get(formula,0)
    return dimensions[:count]


def turnover(rows,quarters,formula=None,example=None):
    return sum((number(r['positive'])+number(r['negative']) for r in rows
        if r['quarter'] in quarters and (formula is None or scope(formula,r)==scope(formula,example))),ZERO)


def ratio(fact,plan):return fact/plan if plan>0 else None


def director_result(case,year,merged,synthetic,recipient,source,branch,facts,plans,settings,rates):
    input_facts=[r for r in (plans if synthetic else facts) if merged or r['branch']==branch]
    input_plan=[r for r in plans if merged or r['branch']==branch]
    quarter_share=number(settings['quarter_share'])/100
    year_share=number(settings['year_share'])/100
    threshold=number(settings['payout_threshold'])
    quarters=range(1,5 if year==2025 else 4)
    paid=[ZERO]*4;annual_paid=ZERO;bonus=ZERO;annual_accrued=ZERO;details=[];notes=set()
    potential=defaultdict(lambda:ZERO);examples={};origins={}
    for row in [*input_plan,*input_facts]:
        values,formula,origin=rates.get(source,branch,row)
        key=(formula,*scope(formula,row))
        examples[key]=row
        if origins.get(key, "exact")=="exact":origins[key]=origin
        if origin!='exact':notes.add('Применены минимальные ставки вместо точной товарной настройки')
    # Clip negative invoice-level accruals before summing, as in the source calculation.
    for row in input_facts:
        values,formula,origin=rates.get(source,branch,row)
        key=(formula,*scope(formula,row));segment=int(row['segment'][1]);k=values[segment]
        amount=number(row['positive']) if k>=0 else number(row['negative'])
        potential[key,row['quarter']]+=amount*k
    for key,example in sorted(examples.items()):
        formula=key[0];scope_paid=ZERO;scope_quarter_potential=ZERO
        for q in quarters:
            check={1} if q==1 else {1,2} if q==2 else {3} if q==3 else {1,2,3,4}
            p=turnover(input_plan,check,formula,example);f=turnover(input_facts,check,formula,example)
            bp=turnover(input_plan,check);bf=turnover(input_facts,check)
            execution=ratio(f,p);department=ratio(bf,bp)
            eligible=q==1 or (execution is not None and execution>=threshold and
                (formula=='Подразделение(Глобально)' or department is not None and department>=threshold))
            possible=potential[key,q]*quarter_share*rates.factor(branch,q)
            payment=possible if eligible else ZERO
            paid[q-1]+=payment;scope_paid+=payment;scope_quarter_potential+=possible
            reasons=[]
            if q==1:reasons.append('I квартал: порог не применяется')
            else:
                if execution is None:reasons.append('Нет положительного плана для проверки')
                elif execution<threshold:reasons.append('Выполнение по формуле ниже порога')
                if formula!='Подразделение(Глобально)' and (department is None or department<threshold):
                    reasons.append('Выполнение подразделения ниже порога')
            if not reasons:reasons.append('Условия выполнены')
            if possible==0:reasons.append('Начисление равно нулю')
            details.append(detail(case,recipient,source,MERGED if merged else branch,formula,example,
                f'{q} квартал',p,f,execution,department,None if q==1 else threshold,possible,payment,ZERO,'; '.join(reasons),origins[key],rates.factor(branch,q)))
        accrued=sum((potential[key,q] for q in quarters),ZERO)*year_share*rates.factor(branch,5)
        annual_accrued+=accrued
        if year==2025:
            p=turnover(input_plan,{1,2,3,4},formula,example);f=turnover(input_facts,{1,2,3,4},formula,example)
            bp=turnover(input_plan,{1,2,3,4});bf=turnover(input_facts,{1,2,3,4})
            execution=ratio(f,p);department=ratio(bf,bp)
            eligible=execution is not None and execution>=threshold and (formula=='Подразделение(Глобально)' or department is not None and department>=threshold)
            payment=accrued if eligible else ZERO
            carry=max(scope_quarter_potential-scope_paid,ZERO) if eligible else ZERO
            annual_paid+=payment;bonus+=carry
            details.append(detail(case,recipient,source,branch,formula,example,'Год',p,f,execution,department,threshold,
                accrued,payment,carry,'Годовой порог выполнен' if eligible else 'Годовой порог не выполнен',origins[key],rates.factor(branch,5)))
    return {'scenario':case,'year':year,'branch':MERGED if merged else branch,'employee':recipient.full_name,
        'employee_id':recipient.pk,'role':'Директор','rate_source':source.full_name,'quarters':paid,
        'annual_paid':annual_paid,'annual_accrued':annual_accrued,'bonus':bonus,
        'total':sum(paid,ZERO)+annual_paid+bonus,'notes':sorted(notes)},details


def detail(case,person,source,branch,formula,item,period,plan,fact,execution,department,threshold,possible,paid,bonus,note,origin,correction):
    return {'scenario':case,'employee':person.full_name,'rate_source':source.full_name,'branch':branch,
        'formula':formula,'planning':item['planning'] if len(scope(formula,item))>=1 else 'Все группы',
        'goods':item['goods'] if len(scope(formula,item))>=2 else 'Все товары',
        'brand':item['brand'] if len(scope(formula,item))>=3 else 'Все бренды',
        'period':period,'plan':plan,'fact':fact,'ratio':execution,'department_ratio':department,'threshold':threshold,
        'possible':possible,'paid':paid,'bonus':bonus,'note':note,'rate_origin':origin,'correction':correction}



@transaction.atomic
def calculate(plan_2025_id,plan_2026_id,version_id,source_moscow,source_tt,target_moscow,target_tt,connector=None):
    plans={2025:SalesPlanScenario.objects.select_for_update().get(pk=plan_2025_id,is_deleted=False),
           2026:SalesPlanScenario.objects.select_for_update().get(pk=plan_2026_id,is_deleted=False)}
    version=GlobalCoeffVersion.objects.select_for_update().get(pk=version_id)
    if version.status!='approved' or version.effective_from.year>2026:
        raise ValidationError('Выберите утверждённую версию директорских коэффициентов модели 2026.')
    ids=[source_moscow,source_tt,target_moscow,target_tt]
    if len(set(ids))!=4:raise ValidationError('Для четырёх ролей выберите четырёх разных сотрудников.')
    people={p.pk:p for p in DirectorEmployee.objects.filter(pk__in=ids)}
    if len(people)!=4:raise ValidationError('Один из выбранных сотрудников не найден.')
    actors=[people[i] for i in ids]
    for year,plan in plans.items():
        if plan.loaded_at is None:raise ValidationError(f'Сначала загрузите выбранный план {year}.')
    plan_rows={year:plan_inputs(plan,year) for year,plan in plans.items()}
    rates=Rates(version)
    # Check model configuration before waiting on the warehouse. Merged cases
    # need each source's rates for the other branch's goods as well.
    unique={tuple(row[k] for k in ('planning','goods','brand')):row
            for rows_ in plan_rows.values() for row in rows_}
    rate_errors=[]
    for source,branch in zip(actors[:2],BRANCHES):
        try:
            for row in unique.values():rates.get(source,branch,row)
        except ValidationError as exc:
            rate_errors.extend(f'{source.full_name}: {message}' for message in exc.messages)
    if rate_errors:raise ValidationError(rate_errors)
    facts,sources=read_sources(plans,version,connector)
    rows=[];details=[];scenarios=[]
    for case in range(1,6):
        year=2025 if case==1 else 2026;merged=case in (4,5);synthetic=case in (3,5)
        title=('2025 · весь год · филиалы отдельно' if case==1 else
               '2026 · I–III кварталы · '+('объединённый филиал' if merged else 'филиалы отдельно')+' · '+('факт = план' if synthetic else 'фактическое выполнение'))
        scenarios.append({'id':case,'title':title,'year':year,'merged':merged,'fact_equals_plan':synthetic})
        for index,branch in enumerate(BRANCHES):
            source=actors[index];recipient=actors[index+2] if case==1 else source
            row,items=director_result(case,year,merged,synthetic,recipient,source,branch,
                facts[year],plan_rows[year],sources[str(year)]['parameters'],rates)
            rows.append(row);details.extend(items)
    config={'plan_2025':plan_2025_id,'plan_2026':plan_2026_id,'coefficient_version':version_id,
        'source_moscow':source_moscow,'source_tt':source_tt,'target_moscow':target_moscow,'target_tt':target_tt}
    rate_inputs=[{'version':key[0],'branch':key[1],'employee_id':key[2],
        'employee':people[key[2]].full_name,'planning':key[3],'goods':key[4],'brand':key[5],
        'matched_goods':rates.matched_keys[key][4],'matched_brand':rates.matched_keys[key][5],
        'values':value[0],'formula':value[1],'origin':value[2]} for key,value in rates.cache.items()]
    payload={'rate_inputs':rate_inputs,'rule':RULE,'scenarios':scenarios,'rows':rows,'details':details,'sources':sources,
        'version':{'id':version.pk,'title':str(version),'revision':version.revision},
        'corrections':[{ 'branch':b,'period':q,'factor':rates.factor(b,q)} for b in BRANCHES for q in range(1,6)],
        'configuration':config,'plans':{str(y):{'title':p.title,'source_version':p.source_version,'loaded_at':str(p.loaded_at)} for y,p in plans.items()},
        'inputs':{'facts':facts,'plans':plan_rows}}
    result=json.loads(json.dumps(payload,ensure_ascii=False,default=str))
    return DirectorScenarioComparison.objects.create(plan_2025=plans[2025],plan_2026=plans[2026],
        coefficient_version=version,configuration=config,results=result)
