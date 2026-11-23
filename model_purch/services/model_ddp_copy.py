"""Reviewed, revision-checked parameter copying between independent model DDP calculations."""
from copy import deepcopy,copy
from hashlib import sha256
from hmac import compare_digest
import json
from types import SimpleNamespace
from django.core import signing
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from ..models import ModelDDPCalculation
from ..model_ddp_forms import RowForm,ParametersForm
from .model_ddp import FIELDS,FREIGHT_FIELDS,parameters,same_parameter,calculate,mark_calculated,directory_signature
from .model_ddp_references import FILTERS,filter_data,apply_query_filters

SALT='model-ddp-copy-v1'
FILTER_KEYS=('q','currency','status','price_check',*[key for key,label in FILTERS])
MODES=('rows','globals','all')


def filtered_ids(batch,filters):
    ids,_,_=filter_data(batch,SimpleNamespace(GET=filters))
    query=apply_query_filters(batch.rows.all(),filters)
    refs={ref for pk,ref in query.values_list('pk','model_ref').iterator(chunk_size=1000) if ids is None or pk in ids}
    return {pk for pk,ref in batch.rows.values_list('pk','model_ref').iterator(chunk_size=1000) if ref in refs}


def key(row):
    return row.model_ref,row.price_type_ref,row.currency


def plan(target,source,mode,filters):
    if target.pk==source.pk:raise ValidationError('Выберите другой помодельный расчёт.')
    if mode not in MODES:raise ValidationError('Выберите состав копирования.')
    rows=[];samples=[];missing=invalid=matched=0
    global_values={'parameters':{name:source.parameters.get(name) for name in FREIGHT_FIELDS},
                   'rates':{name:source.rates.get(name) for name in ('USD','EUR','CNY')}}
    global_values['rates']['RUB']='1'
    baseline_target=target
    if mode=='all':
        baseline_target=copy(target);baseline_target.parameters=global_values['parameters']
    selected=filtered_ids(target,filters) if mode in ('rows','all') else set()
    if mode in ('rows','all'):
        if not selected:raise ValidationError('В текущем фильтре нет моделей для копирования. Загрузите цены PURCH в текущий расчёт или измените фильтры.')
        wanted={(ref,kind,currency) for pk,ref,kind,currency in target.rows.values_list('pk','model_ref','price_type_ref','currency').iterator(chunk_size=1000) if pk in selected}
        sources={}
        wanted_refs={ref for ref,kind,currency in wanted}
        for original in source.rows.all().order_by('price_type','currency','pk').iterator(chunk_size=500):
            if original.model_ref in wanted_refs:sources.setdefault(original.model_ref,original)
        for row in target.rows.all().iterator(chunk_size=500):
            if row.pk not in selected:continue
            original=sources.get(row.model_ref)
            if original is None:missing+=1;continue
            values={name:value for name,value in parameters(source,original).items() if name in FIELDS and value not in (None,'')}
            form=RowForm(values)
            if not form.is_valid():invalid+=1;continue
            base=parameters(baseline_target,row,False)
            # Resolve directory defaults using the classification that will exist after copying.
            classification_names=('planning_sales','group_goods','brand','purch','kind_purch','supply_type')
            prospective=copy(row)
            prospective.overrides={name:value for name,value in row.overrides.items() if name in classification_names}
            prospective.overrides.update({name:str(form.cleaned_data[name]) for name in classification_names if name in values and form.cleaned_data[name] not in (None,'')})
            directory_base=parameters(baseline_target,prospective)
            for name in ('container_volume','duty_percent'):base[name]=directory_base[name]
            current=parameters(target,row)
            overrides=deepcopy(row.overrides)
            for name in values:
                value=form.cleaned_data[name]
                value=None if value in (None,'') else str(value)
                if value is None:continue
                if same_parameter(name,value,base.get(name)):overrides.pop(name,None)
                else:overrides[name]=value
            reviewed={name:str(form.cleaned_data[name]) for name in values if form.cleaned_data[name] not in (None,'')}
            rows.append({'id':row.pk,'source_id':original.pk,'overrides':overrides,'reviewed':reviewed})
            matched+=1
            if len(samples)<10:
                samples.append({'name':row.model_name,'kind':row.price_type,'currency':row.currency,'fields':len(values),
                                'changes':[{'label':RowForm.base_fields[name].label,'before':current.get(name),'after':value} for name,value in reviewed.items() if not same_parameter(name,value,current.get(name))]})
    # Bind both revisions and all reviewed assignments; directory changes require another preview.
    payload={'target':target.pk,'revision':target.revision,'source':source.pk,'source_revision':source.revision,
             'mode':mode,'filters':filters,'selected':sorted(selected),'rows':rows,
             'globals':global_values if mode in ('globals','all') else None}
    digest=sha256(json.dumps(payload,sort_keys=True,ensure_ascii=False,default=str).encode()).hexdigest()
    return {'payload':payload,'digest':digest,'selected':len(selected),'matched':matched,
            'missing':missing,'invalid':invalid,'samples':samples,'source':source,'mode':mode,
            'global_values':global_values,'filters':filters,
            'global_fields':[(ParametersForm.base_fields[name].label,source.parameters.get(name),target.parameters.get(name)) for name in FREIGHT_FIELDS],
            'filter_labels':[(dict(FILTERS).get(name,{'q':'Поиск','currency':'Валюта','status':'Состояние'}.get(name,name)),value) for name,value in filters.items() if value]}


def preview(target,source,mode,request):
    filters={name:request.GET.get(name,'') for name in FILTER_KEYS}
    result=plan(target,source,mode,filters)
    if mode=='rows' and not result['matched']:
        raise ValidationError('В источнике нет подходящих строк с корректными параметрами. Совпадение: ссылка модели.')
    token=signing.dumps({'target':target.pk,'revision':target.revision,'user':request.user.pk,
                        'source':source.pk,'source_revision':source.revision,'mode':mode,
                        'filters':filters,'digest':result['digest']},salt=SALT,compress=True)
    return result,token


@transaction.atomic
def apply(target_id,token,user):
    try:data=signing.loads(token,salt=SALT,max_age=1800)
    except signing.BadSignature:raise ValidationError('Предпросмотр устарел. Повторите выбор источника.')
    if data.get('target')!=target_id or data.get('user')!=user.pk:
        raise ValidationError('Предпросмотр относится к другому пользователю или расчёту.')
    batches={item.pk:item for item in ModelDDPCalculation.objects.select_for_update().filter(pk__in=[target_id,data['source']]).order_by('pk')}
    if len(batches)!=2:raise ValidationError('Расчёт-источник недоступен.')
    target,source=batches[target_id],batches[data['source']]
    if target.revision!=data['revision'] or source.revision!=data['source_revision']:
        raise ValidationError('Источник или текущий расчёт изменился. Повторите предпросмотр.')
    result=plan(target,source,data['mode'],data['filters'])
    if not compare_digest(result['digest'],data['digest']):
        raise ValidationError('Параметры или область копирования изменились. Повторите предпросмотр.')
    global_copy=data['mode'] in ('globals','all')
    prior_stale=target.needs_recalculation or target.source_metadata.get('directory_signature')!=directory_signature(target)
    assignments={item['id']:item['overrides'] for item in result['payload']['rows']}
    if global_copy:
        target.parameters={name:value for name,value in result['global_values']['parameters'].items()}
        target.rates=deepcopy(result['global_values']['rates'])
        target.source_scenario=None
        target.source_metadata={'name':source.name,'model_calculation_id':source.pk,'source_revision':source.revision,
                                'rates_date':str(source.as_of),'copied_at':timezone.now().isoformat()}
    target.source_metadata={**target.source_metadata,'last_model_parameter_copy':{
        'source':source.pk,'name':source.name,'mode':data['mode'],'filters':data['filters'],
        'rows':len(assignments),'copied_at':timezone.now().isoformat()}}
    pending=[];errors=calculated=0
    for row in target.rows.all().iterator(chunk_size=250):
        if not global_copy and row.pk not in assignments:continue
        if row.pk in assignments:row.overrides=assignments[row.pk]
        row.result=calculate(target,row);pending.append(row);calculated+=1
        if row.result.get('status')=='error':errors+=1
        if len(pending)==250:
            target.rows.model.objects.bulk_update(pending,['overrides','result'],batch_size=200);pending=[]
    if pending:target.rows.model.objects.bulk_update(pending,['overrides','result'],batch_size=200)
    target.revision+=1;target.calculated_at=timezone.now();mark_calculated(target)
    target.needs_recalculation=(not global_copy and prior_stale and len(assignments)<target.rows.count()) or target.rows.filter(result={}).exists() or target.rows.filter(result__stale=True).exists()
    target.save()
    return {'copied':len(assignments),'calculated':calculated,'errors':errors,
            'missing':result['missing'],'invalid':result['invalid']}
