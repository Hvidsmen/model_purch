"""Private calculations from immutable uploaded rate/identity snapshots."""
from collections import defaultdict
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from django.core.exceptions import ValidationError
from .coefficient_iteration import mapping_rows, norm, HISTORICAL_DIRECTORS
from .heat_exclusion import is_heat
from .sales_plans import number
from .subdivision_scope import included

RULE='coefficient-iteration-v1'
ZERO=Decimal(0)
MOSCOW='Д-Москва'
TT='ТЕРМОТРЕЙД'
MERGED='Д-Москва + ТЕРМОТРЕЙД'


def branch(value):
    value=str(value).strip()
    return TT if norm(value)==norm(TT) else MOSCOW if norm(value)==norm(MOSCOW) else value


def key(values):
    return tuple(norm(value) if str(value or '').strip() not in ('','*') else '*' for value in values)


class RateBook:
    def __init__(self,payload,mappings,subdivisions,people):
        self.cells={};self.labels={};self.groups={};self.minimums={}
        self.targets=defaultdict(set);self.people=people;self.sources={}
        for row in mapping_rows(payload):
            value=mappings.get(row['id'],{});block=payload[row['source']]['blocks'][row['block']]
            if row['source']=='branches':
                if value.get('subdivision') not in subdivisions:raise ValidationError('Подтвердите все подразделения в сопоставлении.')
                targets=[(branch(subdivisions[value['subdivision']]),row['kind'])]
            else:
                person=value.get('employee')
                if person not in people or not value.get('subdivisions'):raise ValidationError('Подтвердите всех директоров и их подразделения.')
                if value.get('employee_2025') and value['employee_2025'] not in people:raise ValidationError('Не найден исторический директор.')
                if norm(row['label']) in HISTORICAL_DIRECTORS and not value.get('employee_2025'):
                    raise ValidationError('Для блоков Волкова и Галайды выберите директора в 2025 году и подтвердите сопоставление заново.')
                token=row['id'];targets=[]
                for sub in value['subdivisions']:
                    if sub not in subdivisions:raise ValidationError('Не найдено подразделение директора.')
                    self.targets[token].add(branch(subdivisions[sub]))
                self.sources[token]={'employee':person,'employee_2025':value.get('employee_2025') or person,'label':row['label']}
                # The user explicitly identified historical recipients/branches.
                self.sources[token]['historical_branch']=(TT if norm(row['label']).startswith('волков ') else MOSCOW) if norm(row['label']) in HISTORICAL_DIRECTORS else None
                targets=[(token,'Директора')]
            for target in targets:
                for entry in block['entries']:
                    identity=(*target,*key(entry['key']));rates=tuple(number(v).quantize(Decimal('0.000000000000000001')) for v in entry['rates'])
                    if len(rates)!=5 or any(abs(v)>1 for v in rates):raise ValidationError('Некорректные K0–K4.')
                    if identity in self.cells and self.cells[identity]!=rates:raise ValidationError('Конфликт ставок после сопоставления.')
                    self.cells[identity]=rates;self.labels[identity]=list(entry['key'])
                    group=(*target,key(entry['key'])[0]);minimum=min(rates)
                    self.groups[group]=min(self.groups.get(group,minimum),minimum)
                    self.minimums[target]=min(self.minimums.get(target,minimum),minimum)

    def get(self,scope,kind,dimensions,year):
        if kind!='Директора' and scope==TT:scope=MOSCOW  # Explicit 2025 TT=Moscow; later TT is merged.
        p,g,b=key(dimensions);target=(scope,kind)
        for goods,brand_,origin in ((g,b,'Точная ставка'),(g,'*','Строка *'),('*',b,'Строка *'),('*','*','Строка *')):
            cell=(*target,p,goods,brand_)
            if cell in self.cells:return self.cells[cell],origin
        value=self.groups.get((*target,p),self.minimums.get(target))
        if value is None:raise ValidationError(f'Нет ставок: {scope} · {kind}. Проверьте привязки загруженного файла.')
        return (value,)*5,'Минимум группы' if (*target,p) in self.groups else 'Минимум таблицы'

    def classification(self,scope,kind,dimensions):
        if kind!='Директора' and scope==TT:scope=MOSCOW
        p,g,b=key(dimensions)
        for goods,brand_ in ((g,b),(g,'*'),('*',b),('*','*')):
            identity=(scope,kind,p,goods,brand_)
            if identity in self.cells:return tuple(value or '*' for value in self.labels[identity])
        return dimensions

    def directors_for(self,sub,year):
        seen={}
        for token,targets in self.targets.items():
            source=self.sources[token]
            if year==2025:
                targets={source['historical_branch']} if source['historical_branch'] else targets
                matches=sub in targets;person=source['employee_2025']
            else:
                matches=bool(targets & {MOSCOW,TT}) if sub==MERGED else sub in targets
                person=source['employee']
            if not matches:continue
            signature={cell[2:]:value for cell,value in self.cells.items() if cell[:2]==(token,'Директора')}
            if person in seen:
                if seen[person]!=signature:raise ValidationError('После объединения Москвы и ТТ у директора разные ставки. Уточните привязки.')
                continue
            seen[person]=signature
            yield token,person

    def coefficients_2025(self,branches):
        records=[]
        for sub in branches:
            rate_branch=MOSCOW if sub==TT else sub
            for (scope,kind,p,g,b),rates in self.cells.items():
                if scope!=rate_branch or kind=='Директора':continue
                p,g,b=self.labels[scope,kind,p,g,b]
                records.append({'subdivision':sub,'kind':kind,'planning':p,'goods':g,'brand':'' if b=='*' else b,
                                **{f'k{i}':str(v) for i,v in enumerate(rates)}})
            for kind in ('Политики','Продажи'):self.get(sub,kind,('*','*','*'),2025)
        return records


def plan_rows(plan,year):
    if plan.loaded_at is None or plan.is_deleted:raise ValidationError(f'Сначала загрузите план {year}.')
    fields=('plan_date','subdivision','planning_group_sales','group','brand','amount_usd','segment_amounts')
    result=[]
    for day,sub,p,g,b,amount,segments in plan.lines.exclude(subdivision='').order_by().values_list(*fields).iterator(chunk_size=2000):
        if day.year!=year:raise ValidationError(f'В плане {year} присутствует другой год: {day.year}.')
        if not included(sub):continue
        values=[number(segments.get(f'O{i}')) for i in range(5)];amount=number(amount)
        if abs(sum(values,ZERO)-amount)>max(Decimal('.0001'),abs(amount)*Decimal('.00000001')):
            raise ValidationError(f'В плане {year} сумма O0–O4 не совпадает с планом. Перезагрузите план.')
        result.append({'date':day,'branch':branch(sub),'planning':p or '*','goods':g or '*','brand':b or '*',
                       'amount':amount,'segments':values})
    if not result:raise ValidationError(f'Выбранный план {year} пуст.')
    return result


def empty_row(scenario,year,sub,kind,person='',employee_id=None):
    return {'scenario':scenario,'year':year,'branch':sub,'kind':kind,'employee':person,'employee_id':employee_id,
            'quarters':[ZERO]*4,'possible':[ZERO]*4,'annual_quarters':[ZERO]*4,
            'annual_accrued':ZERO,'annual_paid':ZERO,'bonus':ZERO,'total':ZERO}


def at_full_plan(rows,rates,year,quarter_share,year_share,factor=lambda *args:Decimal(1)):
    scenario='moscow_q4_2026' if year==2026 else 'full_2027'
    grouped={};details=[]
    selected=[r for r in rows if year!=2026 or r['date'].month>=10 and r['branch'] in (MOSCOW,TT)]
    if year==2026 and {r['branch'] for r in selected}!={MOSCOW,TT}:
        raise ValidationError('Для IV квартала 2026 нужны исходные планы и Москвы, и Термотрейда.')
    for item in selected:
        sub=MERGED if item['branch'] in (MOSCOW,TT) else item['branch'];q=(item['date'].month-1)//3+1
        dimensions=(item['planning'],item['goods'],item['brand'])
        actors=[('Политики','',None),('Продажи','',None),*[(token,rates.people[person],person) for token,person in rates.directors_for(sub,year)]]
        for actor,person,person_id in actors:
            kind='Директора' if person_id is not None else actor
            if kind=='Политики' and is_heat(item['planning']):continue
            scope=actor if person_id is not None else MOSCOW if sub==MERGED else sub
            values,origin=rates.get(scope,kind,dimensions,year)
            full=sum((max(amount*k,ZERO) for amount,k in zip(item['segments'],values)),ZERO)
            quarterly=full*quarter_share/100*factor(sub,q)
            annual=full*year_share/100*factor(sub,5)
            identity=(sub,kind,person_id)
            row=grouped.setdefault(identity,empty_row(scenario,year,sub,kind,person,person_id))
            row['quarters'][q-1]+=quarterly;row['possible'][q-1]+=quarterly;row['annual_quarters'][q-1]+=annual
            details.append({'scenario':scenario,'year':year,'branch':sub,'original_branch':item['branch'],'kind':kind,
                'employee':person,'date':item['date'].isoformat(),'quarter':q,'planning':dimensions[0],'goods':dimensions[1],
                'brand':dimensions[2],'plan':item['amount'],'fact':item['amount'],'execution':Decimal(1) if item['amount']>0 else None,
                'segments':item['segments'],'rates':values,'origin':origin,'possible':quarterly,'paid':quarterly,'annual_contribution':annual})
    if not grouped:raise ValidationError(f'Нет строк для сценария {year}.')
    for row in grouped.values():
        row['annual_accrued']=sum(row['annual_quarters'],ZERO);row['annual_paid']=row['annual_accrued']
        row['total']=sum(row['quarters'],ZERO)+row['annual_paid']
    return list(grouped.values()),details
