"""Read one completed immutable scenario; never query mutable DWH result tables."""
from collections import defaultdict
from decimal import Decimal
from django.core.exceptions import ValidationError
from django.urls import reverse
from .manager_detail import manager_detail_report
from .motivation_performance import presentation_rows, DEPARTMENT
from .reports_next import period_code

KINDS={'Политики':'pol','Продажи':'sales','Директора':'dir','Кураторы':'cur'}
TITLES={'team':'Премии команды','mine':'Моя премия','detail':'Детальный расчёт','registry':'Реестр','executive':'Обзор для генерального директора'}
ZERO=Decimal(0)


def records(run,dataset):
    chunks=run.chunks.filter(dataset=dataset)
    if run.scenario=='combined' or dataset in ('mapping','deals') or dataset.endswith('_cur'):
        chunks=chunks.filter(mode='combined')
    for chunk in chunks.only('mode','payload').order_by('mode','sequence').iterator(chunk_size=16):
        for row in chunk.payload:
            code=period_code(str(row.get('КварталМотивации','')))
            if dataset.startswith('annual_'):code=str(row.get('Квартал',''))
            mode='separate' if run.scenario=='separate' and code in '123' and code else 'combined'
            if dataset in ('mapping','deals') or dataset.endswith('_cur'):mode='combined'
            if chunk.mode==mode:yield row


def options(run):
    from django.core.cache import cache
    cache_key=f'shareholder-options-v1:{run.pk}:{run.completed_at.isoformat()}' if run.state=='completed' and run.completed_at else None
    if cache_key:
        saved=cache.get(cache_key)
        if saved is not None:return saved
    values={key:set() for key in ('subdivision','author','planning','goods','brand')}
    fields=dict(subdivision='Подразделение',author='АвторСделки',planning='ГруппаПлановПродаж',goods='ГруппаТоваров',brand='Марка')
    for tag in KINDS.values():
        for row in records(run,'report_'+tag):
            if period_code(row['КварталМотивации']) not in [*map(str,run.quarters),'5']:continue
            for key,field in fields.items():
                if row.get(field):values[key].add(row[field])
    result={field:sorted(value) for field,value in values.items()}
    if cache_key:cache.set(cache_key,result,3600)
    return result


def selection(run,query):
    allowed=[*map(str,run.quarters),'5']
    periods=list(dict.fromkeys(query.getlist('period'))) or allowed
    kinds=list(dict.fromkeys(query.getlist('type'))) or list(KINDS)
    if any(v not in allowed for v in periods) or any(v not in KINDS for v in kinds):raise ValidationError('Недоступный период или вид мотивации.')
    if query.get('currency','USD') not in ('USD','RUB'):raise ValidationError('Выберите USD или RUB.')
    for field in ('subdivision','author','planning','goods','brand'):
        if len(query.getlist(field))>100 or any(len(v)>255 for v in query.getlist(field)):raise ValidationError('Слишком много значений фильтра.')
    return periods,kinds


def matches(row,query,*,author=True,products=True):
    fields={'subdivision':'Подразделение'}
    if author:fields['author']='АвторСделки'
    if products:fields.update(planning='ГруппаПлановПродаж',goods='ГруппаТоваров',brand='Марка')
    return all(not query.getlist(key) or row.get(field) in query.getlist(key) for key,field in fields.items())


def filtered_performance(source,stored):
    """Match native personal_windows on the filtered product set; global bases stay whole."""
    dimensions=('Подразделение','Год','КварталМотивации','VariationCalculate')
    windows={};global_contexts=set()
    for row in source:
        context=tuple(str(row.get(key) or '') for key in dimensions)
        formula=row.get('VariationCalculate') or ''
        if formula=='Подразделение(Глобально)':
            global_contexts.add(context);continue
        planning=row.get('ГруппаПлановПродаж') if formula in ('Марка(Бренд)','Группа товаров','Группа планов продаж') else ''
        goods=row.get('ГруппаТоваров') if formula in ('Марка(Бренд)','Группа товаров') else ''
        brand=row.get('Марка') if formula=='Марка(Бренд)' else ''
        key=(*context,row.get('АвторСделки') or '',planning,goods,brand)
        values=windows.setdefault(key,[None,None])
        for index,field in enumerate(('СуммаПланUSD','СуммаОтгрузокUSD')):
            if row.get(field) is not None:
                value=Decimal(str(row[field]));values[index]=max(values[index],value) if values[index] is not None else value
    totals={}
    for key,values in windows.items():
        item=totals.setdefault(key[:5],{**dict(zip(dimensions,key[:4])),'ScopeAuthor':key[4],'Plan_':ZERO,'Fact_':ZERO})
        item['Plan_']+=values[0] or ZERO;item['Fact_']+=values[1] or ZERO
    result=list(totals.values())
    result.extend(row for row in stored if tuple(str(row.get(key) or '') for key in dimensions) in global_contexts)
    return result


def data_for(run,query,*,detailed=True):
    if run.state!='completed':raise ValidationError('Результаты доступны только после успешного завершения всего задания.')
    periods,kinds=selection(run,query);currency=query.get('currency','USD')
    from .closing_coefficient import ReportMultipliers
    multipliers=ReportMultipliers(physical=True,mode=run.metadata.get('manager_correction_mode','legacy'))
    sections=[];rows=[];performance=[];department=[]
    for kind in kinds:
        tag=KINDS[kind]
        source=multipliers.apply([row for row in records(run,'report_'+tag) if period_code(row['КварталМотивации']) in periods])
        search=query.get('search','').strip().casefold()
        filtered=[dict(row,ТипКоэф=kind) for row in source if matches(row,query) and (not search or search in str(row.get('АвторСделки') or '').casefold())]
        if detailed:
            data=manager_detail_report(filtered,currency,performance_rows=source)
        else:
            from .first_quarter_inputs import normalize
            from .manager_detail import period_motivation_summary
            data={'period_summary':period_motivation_summary(normalize(filtered),currency),'currency':currency}
        perf=filtered_performance(filtered,records(run,'perf_'+tag))
        contexts={(r['Подразделение'],str(r['Год']),r['КварталМотивации']) for r in filtered}
        dept=[row for row in records(run,'dept_'+tag) if (row['Подразделение'],str(row['Год']),row['КварталМотивации']) in contexts]
        data['motivation_performance']=perf;data['department_performance']=dept
        performance.extend(presentation_rows(perf,kind));department.extend(presentation_rows(dept,DEPARTMENT))
        sections.append({'kind':kind,'data':data})
        annual=defaultdict(lambda:ZERO)
        for row in multipliers.apply(records(run,'annual_'+tag)):
            if matches(row,query):annual[(row.get('Подразделение') or '—',row.get('АвторСделки') or '—',str(row['Квартал']))]+=Decimal(str(row.get('AnnualRUB' if currency=='RUB' else 'AnnualUSD') or 0))
        summary=data['period_summary']
        for item in summary['rows']:
            for index,column in enumerate(summary['columns']):
                amount=item['values'][index]
                if amount is None:continue
                code=period_code(column['label'])
                target=query.copy();target.pop('export',None);target.pop('audit',None)
                target.setlist('period',[code]);target.setlist('type',[kind]);target.setlist('author',[item['author']]);target.setlist('subdivision',[item['subdivision']])
                rows.append({'subdivision':item['subdivision'],'author':item['author'],'unallocated':item.get('unallocated',False),
                    'kind':kind,'period':column['label'],'code':code,'amount':amount,'bonus':ZERO,'total':amount,'currency':currency,
                    'possible':item.get('possible_values',[None]*len(summary['columns']))[index],
                    'annual_accrual':annual[(item['subdivision'],item['author'],code)] if code in '1234' else None,
                    'detail_url':reverse('motivation_shareholder_report',args=[run.pk,'detail'])+'?'+target.urlencode()})
    return {'rows':rows,'sections':sections,'plan_fact':performance,'department_plan_fact':department,'kinds':kinds,
        'has_unallocated':any(row['unallocated'] for row in rows),'total':sum((row['total'] for row in rows),ZERO),
        'currency':currency,'payout_threshold':Decimal(run.configuration['payout_threshold'])}


def macro_rows(ledger,periods):
    groups={}
    for row in ledger['rows']:
        key=(row['subdivision'],row['kind'])
        item=groups.setdefault(key,{'subdivision':key[0],'kind':key[1],'values':{code:{'possible':ZERO,'total':ZERO} for code in periods},'possible':ZERO,'total':ZERO})
        item['values'][row['code']]['possible']+=row['possible'] or ZERO;item['values'][row['code']]['total']+=row['total']
        item['possible']+=row['possible'] or ZERO;item['total']+=row['total']
    for item in groups.values():item['cells']=[item['values'][code] for code in periods]
    return sorted(groups.values(),key=lambda v:(v['subdivision'],v['kind']))


def audit_rows(run,query):
    """Physical source branch remains visible even in the combined scenario."""
    periods,_=selection(run,query)
    for row in records(run,'deals'):
        day=str(row['ClosedDate'])[:10];quarter=str((int(day[5:7])-1)//3+1)
        if quarter not in periods and '5' not in periods:continue
        if query.get('start') and day<query['start']:continue
        if query.get('end') and day>query['end']:continue
        if query.getlist('author') and row['AssignedAuthor'] not in query.getlist('author'):continue
        if query.get('search','').strip().casefold() not in str(row['AssignedAuthor'] or '').casefold():continue
        branch=row['SourceBranch']
        if run.scenario=='combined' or quarter=='4':branch='Д-Москва'
        if query.getlist('subdivision') and branch not in query.getlist('subdivision'):continue
        fields={'planning':'ГруппаПлановПродаж','goods':'ГруппаТоваров','brand':'Марка'}
        if any(query.getlist(key) and row.get(field) not in query.getlist(key) for key,field in fields.items()):continue
        yield row
