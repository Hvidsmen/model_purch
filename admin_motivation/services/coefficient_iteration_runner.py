"""Persist private input/result snapshots; never export or approve live rates."""
from collections import defaultdict
from datetime import date
from decimal import Decimal
from hashlib import sha256
import json,logging,uuid
from types import SimpleNamespace
from django.core.exceptions import ValidationError
from django.db import transaction,IntegrityError
from django.utils import timezone
from ..models import (CoefficientIteration,CoefficientIterationRun,SalesPlanScenario,Subdivision,
 DirectorEmployee,GlobalCoeffVersion,SubdivisionDirectorCoeff,SubdivisionManagerCoeff)
from .coefficient_iteration_calculation import RateBook,plan_rows,at_full_plan,branch,key,MOSCOW,TT,MERGED,ZERO,RULE,empty_row
from .sales_plans import number

SCENARIOS={'moscow_q4_2026':(2026,'Москва + ТТ · IV квартал 2026 · 100%'),
           'full_2027':(2027,'Все подразделения · 2027 · 100%'),
           'actual_2025':(2025,'Все подразделения · факт 2025 · новые ставки')}


def safe(value):return json.loads(json.dumps(value,default=str,ensure_ascii=False))


def formulas_snapshot(version):
    from .subdivision_aliases import mapping
    corrections=[]
    for sub,period,value in SubdivisionManagerCoeff.objects.filter(version=version).values_list('subdivision__subdivision_key','kind__name','coeff'):
        code=str(period).casefold().replace(' ','').replace('.','')
        q={'1кв':1,'2кв':2,'3кв':3,'4кв':4,'год':5,'year':5}.get(code)
        if q is None:raise ValidationError('Неизвестный период поправки подразделения: '+str(period))
        corrections.append([branch(sub),q,str(number(value))])
    formulas=list(SubdivisionDirectorCoeff.objects.filter(version=version).values_list(
        'subdivision__subdivision_key','employee_id','goods__planning_group_sales','goods__group','goods__brand','variation_calculate__variation_name'))
    return {'version_id':version.pk,'version_name':str(version),'default':version.fallback_variation,
            'aliases':mapping(),'directors':formulas,'corrections':corrections}


class DirectorRates:
    def __init__(self,rates,token,snapshot):
        self.rates=rates;self.token=token;self.snapshot=snapshot
        self.formulas={};self.corrections={}
        for sub,person,p,g,b,formula in snapshot['directors']:
            identity=(branch(sub),person,*key((p,g,b)))
            if identity in self.formulas and self.formulas[identity]!=formula:raise ValidationError('Неоднозначные формулы директора.')
            self.formulas[identity]=formula
        for sub,period,value in snapshot['corrections']:
            identity=(sub,period)
            if identity in self.corrections:raise ValidationError('Неоднозначные поправки подразделения.')
            self.corrections[identity]=number(value)

    def rate_branch(self,sub):
        aliases={branch(k):branch(v) for k,v in self.snapshot['aliases'].items()}
        return aliases.get(sub,sub)

    def factor(self,sub,period):return self.corrections.get((self.rate_branch(sub),period),Decimal(1))

    def get(self,source,sub,row):
        from .sql_export import director_variation
        from .director_scenarios import FORMULAS
        dims=(row['planning'],row['goods'],row['brand'])
        values,origin=self.rates.get(self.token,'Директора',dims,2025)
        formula=self.formulas.get((self.rate_branch(sub),source.pk,*key(dims))) or self.snapshot['default'] or director_variation(SimpleNamespace(planning_group_sales=row['planning']))
        if formula not in FORMULAS:raise ValidationError('Неизвестная формула директора: '+str(formula))
        return values,formula,origin


def validate_directors_2025(plans,rates,snapshot):
    # The native fact uses the uploaded sales classification. Require the same
    # classification matrix before reusing it for directors (no lost dimensions).
    for sub in {r['branch'] for r in plans}:
        if not list(rates.directors_for(sub,2025)):raise ValidationError('Не назначен директор: '+sub)
        sales_scope=MOSCOW if sub==TT else sub
        sales_keys={cell[2:] for cell in rates.cells if cell[:2]==(sales_scope,'Продажи')}
        for token,_ in rates.directors_for(sub,2025):
            director_keys={cell[2:] for cell in rates.cells if cell[:2]==(token,'Директора')}
            if sales_keys!=director_keys:raise ValidationError('Для директорского факта 2025 нужны одинаковые товарные ключи блоков продаж и директоров: '+sub)
            adapter=DirectorRates(rates,token,snapshot)
            source=SimpleNamespace(pk=rates.sources[token]['employee'])
            unique={tuple(r[k] for k in ('planning','goods','brand')):r for r in plans if r['branch']==sub}
            from .director_scenarios import scope
            for item in unique.values():
                _,raw_formula,_=adapter.get(source,sub,item)
                p,g,b=rates.classification(sub,'Продажи',(item['planning'],item['goods'],item['brand']))
                classified={**item,'planning':p,'goods':g,'brand':b}
                _,classified_formula,_=adapter.get(source,sub,classified)
                if raw_formula!=classified_formula or key(scope(raw_formula,item))!=key(scope(raw_formula,classified)):
                    raise ValidationError('Матрица файла укрупняет товарные ключи, необходимые для формулы директора: '+sub)


def directors_2025(cursor,plans,rates,settings,snapshot):
    from .director_scenarios import director_result
    validate_directors_2025(plans,rates,snapshot)
    cursor.execute('''SELECT Подразделение,Квартал,ГруппаПлановПродаж,ГруппаТоваров,Марка,СегментСкидки,
 SUM(CASE WHEN СуммаОтгрузокUSD>0 THEN СуммаОтгрузокUSD ELSE 0 END),
 SUM(CASE WHEN СуммаОтгрузокUSD<0 THEN СуммаОтгрузокUSD ELSE 0 END)
 FROM #iteration_fact WHERE Год=2025 AND LTRIM(RTRIM(ТипКоэф))=N'Продажи'
 GROUP BY Подразделение,Квартал,ГруппаПлановПродаж,ГруппаТоваров,Марка,СегментСкидки''')
    facts=[]
    for sub,q,p,g,b,segment,pos,neg in cursor.fetchall():
        segment=str(segment or '').strip().upper().replace('О','O').replace('K','O')
        if segment not in ('O0','O1','O2','O3','O4'):raise ValidationError('В факте 2025 неизвестный сегмент скидки.')
        facts.append({'branch':branch(sub),'quarter':int(q),'planning':p or '*','goods':g or '*','brand':b or '*',
                      'segment':segment,'positive':number(pos or 0),'negative':number(neg or 0)})
    plan=[]
    for r in plans:
        p,g,b=rates.classification(r['branch'],'Продажи',(r['planning'],r['goods'],r['brand']))
        for i,value in enumerate(r['segments']):
            plan.append({'branch':r['branch'],'quarter':(r['date'].month-1)//3+1,'planning':p,
                'goods':g,'brand':b,'segment':f'O{i}','positive':max(value,ZERO),'negative':min(value,ZERO)})
    rows=[];details=[]
    for sub in sorted({r['branch'] for r in plans}):
        actors=list(rates.directors_for(sub,2025))
        if not actors:raise ValidationError('Для подразделения не назначен директор в итерации: '+sub)
        for token,recipient in actors:
            source_id=rates.sources[token]['employee']
            row,items=director_result('actual_2025',2025,False,False,
                SimpleNamespace(pk=recipient,full_name=rates.people[recipient]),
                SimpleNamespace(pk=source_id,full_name=rates.people[source_id]),sub,facts,plan,settings,DirectorRates(rates,token,snapshot))
            result=empty_row('actual_2025',2025,sub,'Директора',rates.people[recipient],recipient)
            for field in ('quarters','annual_paid','annual_accrued','bonus','total'):result[field]=row[field]
            result['possible']=[row[f'q{q}_possible'] for q in range(1,5)]
            result['annual_quarters']=[row[f'annual_q{q}'] for q in range(1,5)]
            rows.append(result);details.extend([{**item,'kind':'Директора','year':2025} for item in items])
    return rows,details


def start(iteration_id,owner,configuration,revision,*,recover=False):
    with transaction.atomic():
        obj=CoefficientIteration.objects.select_for_update().get(pk=iteration_id,owner=owner)
        if not obj.confirmed or obj.revision!=revision:raise ValidationError('Сопоставление изменилось. Проверьте и подтвердите его перед расчётом.')
        if recover:
            # Caller holds the OS operation lock: a previous running record has
            # no live worker and can safely be finalized without deleting results.
            obj.runs.filter(state='running').update(state='failed',error='Предыдущий процесс расчёта прерван. Результаты завершённых сценариев сохранены.',completed_at=timezone.now())
        elif obj.runs.filter(state='running').exists():raise ValidationError('Эта итерация уже рассчитывается. Дождитесь завершения.')
        version=GlobalCoeffVersion.objects.filter(pk=configuration.get('formula_version'),status='approved').first()
        if 'actual_2025' in configuration['scenarios'] and not version:raise ValidationError('Не найдена утверждённая версия формул директоров 2025.')
        inputs={'rule':RULE,'payload':obj.payload,'mappings':obj.mappings,'revision':obj.revision,
                'subdivisions':dict(Subdivision.all_objects.values_list('pk','subdivision_key')),
                'people':dict(DirectorEmployee.objects.values_list('pk','full_name')),
                'formulas':formulas_snapshot(version) if version else {'formula_defaults':[],'directors':[],'corrections':[],'aliases':{},'default':''}}
        # Validate historical choices even when an old confirmed upload predates
        # introduction of employee_2025. Never silently substitute current staff.
        RateBook(obj.payload,obj.mappings,inputs['subdivisions'],inputs['people'])
        return CoefficientIterationRun.objects.create(iteration=obj,configuration=safe(configuration),inputs=safe(inputs))


def execute(run,progress,connector=None):
    from ..conns import connect_database
    from .coefficient_iteration_2025 import read_2025,read_details
    inputs=run.inputs;settings=run.configuration
    rates=RateBook(inputs['payload'],inputs['mappings'],{int(k):v for k,v in inputs['subdivisions'].items()},
                   {int(k):v for k,v in inputs['people'].items()})
    results={};failed=False
    try:
        progress('Проверка загруженных планов и покрытия ставок',1)
        snapshots={};plans={}
        for scenario in settings['scenarios']:
            year=SCENARIOS[scenario][0]
            plan=SalesPlanScenario.objects.get(pk=settings[f'plan_{year}'],is_deleted=False)
            rows=plan_rows(plan,year)
            current=SalesPlanScenario.objects.get(pk=plan.pk)
            if current.loaded_at!=plan.loaded_at or current.source_type!=plan.source_type or current.source_version!=plan.source_version:
                raise ValidationError('План изменился во время чтения. Повторите расчёт.')
            snapshots[str(year)]={'id':plan.pk,'title':plan.title,'source':plan.source_type,'version':plan.source_version,
                'loaded_at':plan.loaded_at,'sha256':sha256(json.dumps(safe(rows),sort_keys=True).encode()).hexdigest(),'rows':rows}
            plans[year]=rows
        inputs['plans']=safe(snapshots);run.inputs=inputs;run.save(update_fields=['inputs'])
        for scenario in SCENARIOS:
            if scenario not in settings['scenarios']:continue
            year,title=SCENARIOS[scenario]
            try:
                if year!=2025:
                    progress('Расчёт '+title,2 if year==2026 else 3)
                    rows,details=at_full_plan(plans[year],rates,year,number(settings['quarter_share']),number(settings['year_share']))
                    # A zero director section must never masquerade as coverage.
                    for sub in {row['branch'] for row in rows}:
                        if not list(rates.directors_for(sub,year)):raise ValidationError('Не назначены директора: '+sub)
                    source={'source':'Сохранённый план; факт равен плану, выполнение 100%'}
                else:
                    validate_directors_2025(plans[year],rates,inputs['formulas'])
                    connection=None
                    try:
                        connection,cursor=(connector or connect_database)('vm-dwh','DataWH')
                        connection.autocommit=True;connection.timeout=0
                        rows,source=read_2025(cursor,plans[year],rates,{**settings,**inputs['formulas']},progress)
                        progress('2025: премии исторических директоров',8)
                        policy_details,historical_formulas=read_details(cursor,settings['payout_threshold'])
                        source['historical_formulas']=historical_formulas
                        directors,details=directors_2025(cursor,plans[year],rates,settings,inputs['formulas'])
                        rows.extend(directors);details=[*policy_details,*details]
                    finally:
                        if connection is not None:connection.close()
                results[scenario]=safe({'state':'completed','title':title,'year':year,'rows':rows,'details':details,'source':source})
            except Exception as exc:
                failed=True
                if isinstance(exc,ValidationError):message=' '.join(exc.messages)
                else:
                    reference=uuid.uuid4().hex[:12]
                    logging.getLogger(__name__).exception('Coefficient iteration %s failed [%s]',run.pk,reference)
                    message='Не удалось выполнить сценарий. Подробности в журнале сервера. Код: '+reference
                results[scenario]={'state':'failed','title':title,'year':year,'error':message}
            run.results=results;run.save(update_fields=['results'])
        run.state='failed' if failed else 'completed'
        run.error='В отдельных сценариях есть ошибки; успешные результаты сохранены отдельно.' if failed else ''
    except Exception as exc:
        run.state='failed';run.error=' '.join(exc.messages) if isinstance(exc,ValidationError) else 'Не удалось прочитать входные данные. Проверьте журнал сервера.'
        if not isinstance(exc,ValidationError):logging.getLogger(__name__).exception('Iteration input read failed')
    finally:
        run.completed_at=timezone.now();run.save(update_fields=['state','error','results','completed_at'])
    return run


def aggregate_rows(rows):
    """Branch/kind totals, branch subtotals, all-branch kind totals and grand total."""
    grouped={}
    def add(identity,row):
        target=grouped.setdefault(identity,empty_row(row['scenario'],row['year'],identity[0],identity[1]))
        for field in ('quarters','possible','annual_quarters'):
            target[field]=[number(a)+number(b) for a,b in zip(target[field],row[field])]
        for field in ('annual_accrued','annual_paid','bonus','total'):target[field]=number(target[field])+number(row[field])
    for row in rows:
        for identity in ((row['branch'],row['kind']),(row['branch'],'ИТОГО по подразделению'),
                         ('Все выбранные подразделения',row['kind']),('ИТОГО','Все выбранные подразделения')):add(identity,row)
    kinds={'Политики':0,'Продажи':1,'Директора':2,'ИТОГО по подразделению':3,'Все выбранные подразделения':4}
    return [grouped[k] for k in sorted(grouped,key=lambda k:(k[0]=='ИТОГО',k[0]=='Все выбранные подразделения',k[0],kinds.get(k[1],9)))]
