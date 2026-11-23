from decimal import Decimal
from io import BytesIO
import json
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied,ValidationError
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404,redirect,render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods
from base.access import role_for,ADMIN,LOGISTICIAN,ANALYST
from .models import ModelDDPCalculation
from .model_ddp_forms import CreateForm,ParametersForm,RowForm,SourceForm,ModelCopyForm
from .services.model_ddp import mutate,parameters,preview_token,FREIGHT_FIELDS,MODEL_FIELDS,directory_signature
from .services.model_ddp_source import load_prices,empty_reference
from .services.model_ddp_references import filter_data,catalog,apply_query_filters


def access(request):
    role=role_for(request.user,'purchases')
    if role not in (ADMIN,LOGISTICIAN,ANALYST) or request.method=='POST' and role==ANALYST:raise PermissionDenied
    return role


def initial_parameters(batch):
    return {**{k:v for k,v in batch.parameters.items() if k!='groups'},**{code.lower():batch.rates.get(code) for code in ('USD','EUR','CNY')}}


def row_changes(batch,data,optional=False):
    try:ids=json.loads(data.get('rows','[]'))
    except (ValueError,TypeError):raise ValidationError('Некорректный список строк.')
    if optional and ids==[]:return {},{},[]
    if not isinstance(ids,list) or not ids or len(ids)>200 or any(type(i) is not int for i in ids) or len(set(ids))!=len(ids):
        raise ValidationError('Выберите строки текущего расчёта.')
    found=list(batch.rows.filter(pk__in=ids))
    if len(found)!=len(ids):raise ValidationError('Строка не принадлежит выбранному расчёту.')
    changes={};forms={};errors=[]
    for row in found:
        f=RowForm(data,prefix='r'+str(row.pk));forms[row.pk]=f
        if not f.is_valid():
            errors.append(row.model_name+': '+'; '.join(str(e) for values in f.errors.values() for e in values));continue
        changes[row.pk]={k:v if isinstance(v,bool) else None if v in (None,'') else str(v) for k,v in f.cleaned_data.items()}
    return changes,forms,errors


@login_required
@require_http_methods(['GET','HEAD','POST'])
def index(request):
    access(request)
    form=CreateForm(request.POST or None,initial={'as_of':timezone.localdate()})
    if request.method=='POST' and form.is_valid():
        batch=ModelDDPCalculation.objects.create(**form.cleaned_data,created_by=request.user,rates={'RUB':'1'})
        return redirect('model_ddp_detail',pk=batch.pk)
    return render(request,'model_purch/model_ddp_index.html',{'form':form,'page':Paginator(ModelDDPCalculation.objects.select_related('created_by'),25).get_page(request.GET.get('page'))})


@login_required
@require_http_methods(['GET','HEAD','POST'])
def detail(request,pk):
    role=access(request);batch=get_object_or_404(ModelDDPCalculation,pk=pk)
    form=ParametersForm(initial=initial_parameters(batch));source_form=SourceForm();preview=None;token=''
    model_copy_form=ModelCopyForm(target=batch);model_copy_preview=None;model_copy_token=''
    error='';bound_rows={}
    if request.method=='POST':
        action=request.POST.get('action')
        try:
            if str(request.POST.get('revision'))!=str(batch.revision):raise ValidationError('Расчёт изменён в другой вкладке. Обновите страницу.')
            if action=='model_copy_preview':
                from .services.model_ddp_copy import preview as copy_preview
                model_copy_form=ModelCopyForm(request.POST,target=batch)
                if model_copy_form.is_valid():
                    model_copy_preview,model_copy_token=copy_preview(batch,model_copy_form.cleaned_data['source_calculation'],model_copy_form.cleaned_data['mode'],request)
            elif action=='model_copy_apply':
                from .services.model_ddp_copy import apply as copy_apply
                copied=copy_apply(pk,request.POST.get('model_copy_token',''),request.user)
                messages.success(request,f"Скопировано строк: {copied['copied']}. Пересчитано: {copied['calculated']}. Ошибок расчёта: {copied['errors']}. Без совпадения: {copied['missing']}; некорректных строк источника: {copied['invalid']}.")
                return redirect(reverse('model_ddp_detail',args=[pk])+'?'+request.GET.urlencode())
            elif action=='preview':
                source_form=SourceForm(request.POST)
                if source_form.is_valid():preview,token=preview_token(batch,source_form.cleaned_data['source'],request.user)
            elif action=='copy':mutate(pk,batch.revision,'copy',user=request.user,token=request.POST.get('token',''))
            elif action=='load':mutate(pk,batch.revision,'load',rows=load_prices(batch.as_of,ensure_index=role==ADMIN))
            elif action=='parameters':
                form=ParametersForm(request.POST)
                if form.is_valid():
                    data={key:None if value in (None,'') else str(value) for key,value in form.cleaned_data.items()}
                    mutate(pk,batch.revision,'parameters',values={'parameters':{k:v for k,v in data.items() if k not in ('usd','eur','cny')},'rates':{'USD':data['usd'],'EUR':data['eur'],'CNY':data['cny'],'RUB':'1'}})
            elif action=='workflow_calculate':
                form=ParametersForm(request.POST)
                changes,bound_rows,row_errors=row_changes(batch,request.POST,optional=True)
                if row_errors:raise ValidationError(row_errors[:10])
                if form.is_valid():
                    data={key:None if value in (None,'') else str(value) for key,value in form.cleaned_data.items()}
                    mutate(pk,batch.revision,'workflow_calculate',rows=changes,values={
                        'parameters':{k:v for k,v in data.items() if k not in ('usd','eur','cny')},
                        'rates':{'USD':data['usd'],'EUR':data['eur'],'CNY':data['cny'],'RUB':'1'}})
            elif action=='rows_save':
                changes,bound_rows,row_errors=row_changes(batch,request.POST)
                if row_errors:raise ValidationError(row_errors[:10])
                mutate(pk,batch.revision,'rows_save',rows=changes)
            elif action=='calculate':mutate(pk,batch.revision,'calculate')
            elif action=='rows':
                try:ids=json.loads(request.POST.get('rows','[]'))
                except (ValueError,TypeError):raise ValidationError('Некорректный список строк.')
                if not isinstance(ids,list) or not ids or len(ids)>200 or any(not isinstance(i,int) for i in ids) or len(set(ids))!=len(ids):raise ValidationError('Выберите строки текущего расчёта.')
                found=list(batch.rows.filter(pk__in=ids))
                if len(found)!=len(ids):raise ValidationError('Строка не принадлежит выбранному расчёту.')
                changes={}
                for row in found:
                    f=RowForm(request.POST,prefix='r'+str(row.pk))
                    if not f.is_valid():raise ValidationError(row.model_name+': '+'; '.join(str(e) for errors in f.errors.values() for e in errors))
                    changes[row.pk]={k:v if isinstance(v,bool) else None if v in (None,'') else str(v) for k,v in f.cleaned_data.items()}
                mutate(pk,batch.revision,'rows',rows=changes)
            else:raise ValidationError('Неизвестное действие.')
            if action not in ('preview','model_copy_preview') and (action not in ('parameters','workflow_calculate') or form.is_valid()):
                messages.success(request,'Параметры моделей сохранены.' if action=='rows_save' else 'DDP рассчитан. Проверьте результаты и строки с ошибками.' if action in ('rows','calculate','parameters','workflow_calculate') else 'Данные сохранены. Перейдите к расчёту DDP.')
                destination=request.GET.copy()
                if action=='load':destination['step']='review'
                elif action in ('workflow_calculate','calculate','parameters'):destination['step']='calculate'
                return redirect(reverse('model_ddp_detail',args=[pk])+'?'+destination.urlencode())
        except ValidationError as exc:error=' '.join(exc.messages)
    query=batch.rows.all();q=request.GET.get('q','').strip();currency=request.GET.get('currency','');status=request.GET.get('status','');brand=request.GET.get('brand','')
    ids,reference_filters,reference_choices=filter_data(batch,request)
    query=apply_query_filters(query,request.GET)
    from .services.product_options import classification_options
    from .models import Purch,KindPurch
    choices=classification_options()
    choices.update(purch=list(Purch.objects.values_list('name',flat=True)),kind_purch=list(KindPurch.objects.values_list('name',flat=True)))
    for key,values in reference_choices.items():
        if key in ('planning_sales','group_goods','brand','purch','kind_purch'):choices[key]=sorted(set(choices.get(key,[]))|set(values),key=str.casefold)
    # Filter matches select models; keep all prices of each selected model together.
    selected_refs=list(dict.fromkeys(ref for pk,ref in query.order_by('model_name','model_ref','pk').values_list('pk','model_ref').iterator(chunk_size=1000) if ids is None or pk in ids))
    page=Paginator(selected_refs,50).get_page(request.GET.get('page'))
    groups={ref:[] for ref in page.object_list}
    for variant in batch.rows.filter(model_ref__in=list(groups)).order_by('price_type','currency','pk'):
        if empty_reference(variant.source_data.get('unit')):variant.source_data={**variant.source_data,'unit':''}
        groups[variant.model_ref].append(variant)
    page.object_list=[groups[ref][0] for ref in page.object_list]
    for row in page:
        row.price_variants=groups[row.model_ref]
        values=parameters(batch,row)
        row.edit_form=bound_rows.get(row.pk) or RowForm(prefix='r'+str(row.pk),initial=values,reference_choices=choices)
    from django.db.models import Count
    stats=batch.rows.aggregate(total=Count('pk'),zero=Count('pk',filter=Q(price=0)),
        missing_price=Count('pk',filter=Q(price__isnull=True)),
        missing_currency=Count('pk',filter=~Q(currency__in=('USD','EUR','CNY','RUB'))),
        errors=Count('pk',filter=Q(result__status='error')),partial=Count('pk',filter=Q(result__status='partial')),pending=Count('pk',filter=Q(result={})))
    entries,_=catalog(batch)
    stats['missing_supplier']=sum(values['supply_type'] not in ('direct','p2','russian') for pk,values,text in entries)
    needs_recalculation=batch.needs_recalculation or bool(stats['pending']) or batch.source_metadata.get('directory_signature')!=directory_signature(batch)
    step=request.GET.get('step')
    if step not in ('load','review','calculate'):
        step='load' if not batch.loaded_at and not stats['total'] else 'review' if needs_recalculation else 'calculate'
    if request.method=='POST' and request.POST.get('action') in ('preview','copy','model_copy_preview','model_copy_apply','workflow_calculate','parameters'):
        step='calculate'
    rejected_edits=''
    if request.method=='POST' and (error or form.errors):
        if request.POST.get('action')=='workflow_calculate':rejected_edits='both'
        elif request.POST.get('action')=='rows_save':rejected_edits='rows'
    preserved=request.GET.copy();preserved.pop('page',None);preserved['step']=step
    step_urls={}
    for name in ('load','review','calculate'):
        params=preserved.copy();params['step']=name
        step_urls[name]=reverse('model_ddp_detail',args=[pk])+'?'+params.urlencode()
    return render(request,'model_purch/model_ddp_detail.html',{'batch':batch,'rejected_edits':rejected_edits,'expense_fields':[form[name] for name in FREIGHT_FIELDS],'rate_fields':[form[name] for name in ('usd','eur','cny')],'workflow_step':step,'step_urls':step_urls,'stats':stats,'needs_recalculation':needs_recalculation,'price_check':request.GET.get('price_check',''),'model_copy_form':model_copy_form,'model_copy_preview':model_copy_preview,'model_copy_token':model_copy_token,'reference_filters':reference_filters,'can_prepare_index':role==ADMIN,'parameters_form':form,'source_form':source_form,'preview':preview,'preview_fields':[(ParametersForm.base_fields[k].label,v) for k,v in (preview['parameters'].items() if preview else []) if k!='groups'],'token':token,'error':error,'page':page,'row_ids':json.dumps([r.pk for r in page]),'q':q,'currency':currency,'status':status,'brand':brand,'brands':batch.rows.exclude(brand='').order_by('brand').values_list('brand',flat=True).distinct(),'query_string':preserved.urlencode(),'model_total':batch.rows.values('model_ref').distinct().count(),'total':batch.rows.count()})


def safe(value):
    if isinstance(value,str) and value.lstrip().startswith(('=','+','-','@')):return "'"+value
    return value


@login_required
@require_http_methods(['GET','HEAD'])
def export(request,pk):
    access(request);batch=get_object_or_404(ModelDDPCalculation,pk=pk)
    from openpyxl import Workbook
    book=Workbook();meta=book.active;meta.title='Параметры'
    meta.append(['Расчёт',safe(batch.name)]);meta.append(['Дата цен и курсов',str(batch.as_of)]);meta.append(['Ревизия',batch.revision])
    export_stale=batch.needs_recalculation or batch.source_metadata.get('directory_signature')!=directory_signature(batch)
    meta.append(['Состояние расчёта','Требуется пересчёт' if export_stale else 'Рассчитано'])
    meta.append(['Формула','Существующая модель закупок; загран доставка и НР НДС не входят в DDP'])
    for k,v in batch.rates.items():meta.append(['RUB за 1 '+k,safe(v)])
    for k,v in batch.parameters.items():
        if k in FREIGHT_FIELDS:meta.append([k,safe(v)])
    meta.append(['Источник параметров',safe(batch.source_metadata.get('name','Вручную'))])
    sheet=book.create_sheet('Помодельный DDP');sheet.append(['Модель','Ссылка','Группа','Бренд','Вид цены','Валюта','Цена источника','Дата цены','Объём','Контейнер','Пошлина %','Тип поставки','DDP USD','DDP EUR','DDP CNY','DDP RUB','Состояние','EXW USD','Фрахт USD','CIF USD','Таможенный платёж USD','До склада USD','Загран доставка отдельно USD','НР НДС отдельно USD','Единица цены источника','Группа планов продаж','Группа товаров','Марка модели','Закупка / поставщик','Вид закупки','Вес модели, кг','Фрахт контейнера USD','Таможенная ставка %','Доставка за контейнер USD','Загран доставка за контейнер USD','НР НДС %'])
    model_ranges={}
    for row in batch.rows.order_by('model_name','model_ref','price_type','currency','pk').iterator(chunk_size=500):
        model_ranges.setdefault(row.model_ref,[]).append(sheet.max_row+1)
        p=parameters(batch,row);result=row.result;ddp=result.get('ddp',{});c=result.get('components',{})
        values=[row.model_name,row.model_ref,row.planning_group,row.brand,row.price_type,row.currency,row.price,str(row.price_at or ''),*[p.get(k) for k in ('volume','container_volume','duty_percent','supply_type')],*[ddp.get(k) for k in ('USD','EUR','CNY','RUB')],result.get('error') or '; '.join(result.get('warnings',[])) or result.get('status','Не рассчитан'),*[c.get(k) for k in ('exw_usd','freight_usd','cif_usd','customs_payment_usd','warehouse_delivery_usd','foreign_delivery_usd','nr_customs_vat_usd')]]
        # Preserve numeric cells for Excel formulas; escape source text separately.
        numeric_columns={6,8,9,10,12,13,14,15,17,18,19,20,21,22,23}
        for index in numeric_columns:
            if values[index] is not None:
                values[index]=Decimal(str(values[index]))
        values.append(row.source_data.get('unit',''))
        values.extend([p.get(k) for k in ('planning_sales','group_goods','brand','purch','kind_purch','weight_kg',*FREIGHT_FIELDS)])
        if export_stale or row.result.get('stale'):values[16]='Требуется пересчёт · '+str(values[16])
        sheet.append([safe(v) for v in values])
    for positions in model_ranges.values():
        if len(positions)>1:
            for column in (1,2,3,4,9,10,11,12,*range(26,37)):
                sheet.merge_cells(start_row=positions[0],end_row=positions[-1],start_column=column,end_column=column)
    trace=book.create_sheet('Этапы расчёта');trace.append(['Модель','Вид цены','Валюта цены','Этап','Формула','Результат','Валюта результата'])
    for row in batch.rows.all().iterator(chunk_size=500):
        for step in row.result.get('steps',[]):
            trace.append([safe(row.model_name),safe(row.price_type),safe(row.currency),safe(step['label']),safe(step['formula']),Decimal(step['value']),safe(step['currency'])])
    trace.freeze_panes='A2';trace.auto_filter.ref=trace.dimensions
    sheet.freeze_panes='A2';sheet.auto_filter.ref=sheet.dimensions
    output=BytesIO();book.save(output);book.close()
    response=HttpResponse(output.getvalue(),content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet');response['Content-Disposition']=f'attachment; filename="model-ddp-{pk}.xlsx"';return response


@login_required
@require_http_methods(['POST'])
def load_stream(request,pk):
    """Stream real loader milestones; SQL execution has no fabricated percentage."""
    from django.http import StreamingHttpResponse, JsonResponse
    from django.db import close_old_connections
    from threading import Thread, Event
    from queue import Queue, Empty
    role=access(request)
    batch=get_object_or_404(ModelDDPCalculation,pk=pk)
    revision=request.POST.get('revision')
    if str(batch.revision)!=str(revision):
        return JsonResponse({'error':'Расчёт изменён в другой вкладке. Обновите страницу.'},status=409)
    next_query=request.GET.copy();next_query['step']='review'
    next_url=reverse('model_ddp_detail',args=[pk])+'?'+next_query.urlencode()
    queue=Queue()
    disconnected=Event()
    def progress(stage,**data):
        if disconnected.is_set():raise ValidationError('Загрузка прервана до сохранения.')
        queue.put({'stage':stage,**data})
    def work():
        close_old_connections()
        try:
            rows=load_prices(batch.as_of,progress=progress,ensure_index=role==ADMIN)
            progress('сохранение снимка',rows=len(rows))
            mutate(pk,revision,'load',rows=rows)
            queue.put({'done':True,'stage':'Загрузка завершена','rows':len(rows),'next_url':next_url})
        except ValidationError as error:queue.put({'done':True,'error':' '.join(error.messages)})
        except Exception:
            import logging
            from uuid import uuid4
            code=uuid4().hex[:12]
            logging.getLogger(__name__).error('PURCH save diagnostic=%s exception during snapshot save',code)
            queue.put({'done':True,'error':'Ошибка сохранения снимка. Код диагностики: '+code})
        finally:close_old_connections()
    def events():
        thread=Thread(target=work,daemon=True)
        thread.start()
        try:
            while True:
                try:item=queue.get(timeout=1)
                except Empty:item={'heartbeat':True}
                yield json.dumps(item,ensure_ascii=False)+'\n'
                if item.get('done'):break
        finally:disconnected.set()
    response=StreamingHttpResponse(events(),content_type='application/x-ndjson')
    response['Cache-Control']='no-cache, no-store'
    response['X-Accel-Buffering']='no'
    return response
