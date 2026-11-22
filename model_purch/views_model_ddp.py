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
from .model_ddp_forms import CreateForm,ParametersForm,RowForm,SourceForm
from .services.model_ddp import mutate,parameters,preview_token,FREIGHT_FIELDS,MODEL_FIELDS
from .services.model_ddp_source import load_prices


def access(request):
    role=role_for(request.user,'purchases')
    if role not in (ADMIN,LOGISTICIAN,ANALYST) or request.method=='POST' and role==ANALYST:raise PermissionDenied
    return role


def initial_parameters(batch):
    return {**{k:v for k,v in batch.parameters.items() if k!='groups'},**{code.lower():batch.rates.get(code) for code in ('USD','EUR','CNY')}}


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
    access(request);batch=get_object_or_404(ModelDDPCalculation,pk=pk)
    form=ParametersForm(initial=initial_parameters(batch));source_form=SourceForm();preview=None;token=''
    error=''
    if request.method=='POST':
        action=request.POST.get('action')
        try:
            if str(request.POST.get('revision'))!=str(batch.revision):raise ValidationError('Расчёт изменён в другой вкладке. Обновите страницу.')
            if action=='preview':
                source_form=SourceForm(request.POST)
                if source_form.is_valid():preview,token=preview_token(batch,source_form.cleaned_data['source'],request.user)
            elif action=='copy':mutate(pk,batch.revision,'copy',user=request.user,token=request.POST.get('token',''))
            elif action=='load':mutate(pk,batch.revision,'load',rows=load_prices(batch.as_of))
            elif action=='parameters':
                form=ParametersForm(request.POST)
                if form.is_valid():
                    data={key:None if value in (None,'') else str(value) for key,value in form.cleaned_data.items()}
                    mutate(pk,batch.revision,'parameters',values={'parameters':{k:v for k,v in data.items() if k not in ('usd','eur','cny')},'rates':{'USD':data['usd'],'EUR':data['eur'],'CNY':data['cny'],'RUB':'1'}})
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
            if action!='preview' and (action!='parameters' or form.is_valid()):
                messages.success(request,'Данные сохранены. Результаты рассчитаны.' if action in ('rows','calculate','parameters') else 'Данные сохранены. Для обновления DDP нажмите «Рассчитать».')
                return redirect(reverse('model_ddp_detail',args=[pk])+'?'+request.GET.urlencode())
        except ValidationError as exc:error=' '.join(exc.messages)
    query=batch.rows.all();q=request.GET.get('q','').strip();currency=request.GET.get('currency','');status=request.GET.get('status','');brand=request.GET.get('brand','')
    if q:query=query.filter(Q(model_name__icontains=q)|Q(planning_group__icontains=q)|Q(price_type__icontains=q))
    if currency:query=query.filter(currency=currency)
    if brand:query=query.filter(brand=brand)
    if status in ('ready','partial','error'):query=query.filter(result__status=status)
    elif status=='pending':query=query.filter(result={})
    page=Paginator(query,50).get_page(request.GET.get('page'))
    for row in page:
        values=parameters(batch,row);values.update(price_quantity=row.overrides.get('price_quantity') or '1',price_unit_confirmed=row.overrides.get('price_unit_confirmed',False))
        row.edit_form=RowForm(prefix='r'+str(row.pk),initial=values)
    preserved=request.GET.copy();preserved.pop('page',None)
    return render(request,'model_purch/model_ddp_detail.html',{'batch':batch,'parameters_form':form,'source_form':source_form,'preview':preview,'preview_fields':[(ParametersForm.base_fields[k].label,v) for k,v in (preview['parameters'].items() if preview else []) if k!='groups'],'token':token,'error':error,'page':page,'row_ids':json.dumps([r.pk for r in page]),'q':q,'currency':currency,'status':status,'brand':brand,'brands':batch.rows.exclude(brand='').order_by('brand').values_list('brand',flat=True).distinct(),'query_string':preserved.urlencode(),'total':batch.rows.count()})


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
    meta.append(['Формула','Существующая модель закупок; загран доставка и НР НДС не входят в DDP'])
    for k,v in batch.rates.items():meta.append(['RUB за 1 '+k,safe(v)])
    for k,v in batch.parameters.items():
        if k!='groups':meta.append([k,safe(v)])
    meta.append(['Источник параметров',safe(batch.source_metadata.get('name','Вручную'))])
    sheet=book.create_sheet('Помодельный DDP');sheet.append(['Модель','Ссылка','Группа','Бренд','Вид цены','Валюта','Цена источника','Дата цены','Объём','Контейнер','Пошлина %','Тип поставки','DDP USD','DDP EUR','DDP CNY','DDP RUB','Состояние','EXW USD','Фрахт USD','CIF USD','Таможенный платёж USD','До склада USD','Загран доставка отдельно USD','НР НДС отдельно USD','Единица цены источника','Единиц модели в цене','Количество подтверждено','Группа планов продаж','Группа товаров','Марка модели','Закупка / поставщик','Вид закупки','Вес модели, кг','Запас, дни','Остаток, %','Фрахт контейнера USD','Таможенная ставка %','Доставка за контейнер USD','Загран доставка за контейнер USD','НР НДС %'])
    for row in batch.rows.all().iterator(chunk_size=500):
        p=parameters(batch,row);result=row.result;ddp=result.get('ddp',{});c=result.get('components',{})
        values=[row.model_name,row.model_ref,row.planning_group,row.brand,row.price_type,row.currency,row.price,str(row.price_at or ''),*[p.get(k) for k in ('volume','container_volume','duty_percent','supply_type')],*[ddp.get(k) for k in ('USD','EUR','CNY','RUB')],result.get('error') or '; '.join(result.get('warnings',[])) or result.get('status','Не рассчитан'),*[c.get(k) for k in ('exw_usd','freight_usd','cif_usd','customs_payment_usd','warehouse_delivery_usd','foreign_delivery_usd','nr_customs_vat_usd')]]
        # Preserve numeric cells for Excel formulas; escape source text separately.
        numeric_columns={6,8,9,10,12,13,14,15,17,18,19,20,21,22,23}
        for index in numeric_columns:
            if values[index] is not None:
                values[index]=Decimal(str(values[index]))
        values.extend([row.source_data.get('unit',''),Decimal(str(row.overrides.get('price_quantity') or '1')),bool(row.overrides.get('price_unit_confirmed'))])
        values.extend([p.get(k) for k in ('planning_sales','group_goods','brand','purch','kind_purch','weight_kg','stock_cnt_day','percent_stock_end',*FREIGHT_FIELDS)])
        sheet.append([safe(v) for v in values])
    trace=book.create_sheet('Этапы расчёта');trace.append(['Модель','Вид цены','Валюта цены','Этап','Формула','Результат','Валюта результата'])
    for row in batch.rows.all().iterator(chunk_size=500):
        for step in row.result.get('steps',[]):
            trace.append([safe(row.model_name),safe(row.price_type),safe(row.currency),safe(step['label']),safe(step['formula']),Decimal(step['value']),safe(step['currency'])])
    trace.freeze_panes='A2';trace.auto_filter.ref=trace.dimensions
    sheet.freeze_panes='A2';sheet.auto_filter.ref=sheet.dimensions
    output=BytesIO();book.save(output);book.close()
    response=HttpResponse(output.getvalue(),content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet');response['Content-Disposition']=f'attachment; filename="model-ddp-{pk}.xlsx"';return response
