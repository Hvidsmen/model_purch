"""First-upload identity review; never change live coefficients or the roster."""
from django import forms
from django.core.exceptions import PermissionDenied,ValidationError
from django.core.validators import FileExtensionValidator
from django.db import transaction
from django.shortcuts import get_object_or_404,redirect,render
from django.utils import timezone
from django.views.decorators.http import require_http_methods
from base.access import can_access
from .models import CoefficientIteration,Subdivision,DirectorEmployee
from .services.coefficient_iteration import read_file,mapping_rows,suggestions,check_conflicts


class UploadForm(forms.Form):
    branches=forms.FileField(label='Коэффициенты политик и продаж по филиалам',validators=[FileExtensionValidator(['xlsx'])])
    directors=forms.FileField(label='Коэффициенты директоров',validators=[FileExtensionValidator(['xlsx'])])


class MappingForm(forms.Form):
    revision=forms.IntegerField(widget=forms.HiddenInput)

    def __init__(self,iteration,*args,**kwargs):
        super().__init__(*args,**kwargs);self.rows=[]
        branches=Subdivision.all_objects.select_related('chanel','merged_into').order_by('subdivision_name','subdivision_key')
        people=DirectorEmployee.objects.order_by('full_name','pk')
        self.initial['revision']=iteration.revision
        for row in mapping_rows(iteration.payload):
            name=row['id'];value=iteration.mappings.get(name,{})
            if row['source']=='branches':
                field=forms.ModelChoiceField(queryset=branches,required=False,label='Подразделение',initial=value.get('subdivision'))
                field.label_from_instance=lambda sub:f'{sub.subdivision_name} · {sub.subdivision_key} · {sub.chanel}'+(' · объединено' if sub.merged_into_id else '')
                field.widget.attrs['data-searchable-select']=''
                self.fields[name]=field
                self.rows.append({**row,'subdivision':self[name]})
            else:
                field=forms.ModelChoiceField(queryset=people,required=False,label='Директор',initial=value.get('employee'))
                field.label_from_instance=lambda person:f'{person.full_name} · № {person.pk}'+(' · архив' if person.is_archived else '')
                field.widget.attrs['data-searchable-select']=''
                self.fields[name+'_employee']=field
                field=forms.ModelMultipleChoiceField(queryset=branches,required=False,label='Подразделения для этой итерации',initial=value.get('subdivisions',[]))
                field.label_from_instance=lambda sub:f'{sub.subdivision_name} · {sub.subdivision_key} · {sub.chanel}'
                field.widget.attrs['size']=4
                self.fields[name+'_branches']=field
                self.rows.append({**row,'employee':self[name+'_employee'],'branches':self[name+'_branches']})

        for row in self.rows:
            row['complete']=bool(row['subdivision'].value()) if row['source']=='branches' else bool(row['employee'].value() and row['branches'].value())
        self.unresolved=sum(not row['complete'] for row in self.rows)

    def resolved(self,complete=False):
        result={};missing=[]
        for row in self.rows:
            name=row['id']
            if row['source']=='branches':
                sub=self.cleaned_data.get(name)
                result[name]={'subdivision':sub.pk if sub else None}
                if not sub:missing.append(row['label'])
            else:
                person=self.cleaned_data.get(name+'_employee');subs=list(self.cleaned_data.get(name+'_branches',[]))
                result[name]={'employee':person.pk if person else None,'subdivisions':[sub.pk for sub in subs]}
                if not person or not subs:missing.append(row['label'])
        if complete and missing:raise ValidationError('Заполните сопоставление: '+', '.join(missing[:12]))
        return result


def allowed(request):
    if not can_access(request.user,'motivation_editor'):raise PermissionDenied


@require_http_methods(['GET','POST'])
def upload(request):
    allowed(request)
    form=UploadForm(request.POST or None,request.FILES or None)
    if request.method=='POST' and form.is_valid():
        try:
            payload={'branches':read_file(form.cleaned_data['branches']),
                     'directors':read_file(form.cleaned_data['directors'],directors=True)}
            names={kind:{name for block in payload['branches']['blocks'] if block['kind']==kind for name in block['labels']} for kind in ('Политики','Продажи')}
            for kind,other in [('Политики','Продажи'),('Продажи','Политики')]:
                missing=sorted(names[kind]-names[other])
                if missing:payload['branches']['warnings'].append(f'{kind}: есть филиалы, отсутствующие в списке «{other}»: '+', '.join(missing)+'. Для них ставки другого вида не определены этим файлом.')
            if len(mapping_rows(payload))>200:raise ValidationError('В двух файлах больше 200 получателей.')
            obj=CoefficientIteration.objects.create(owner=request.user,payload=payload,mappings=suggestions(payload))
            return redirect('motivation_coefficient_iteration_mapping',pk=obj.pk)
        except ValidationError as exc:form.add_error(None,exc)
    return render(request,'admin_motivation/coefficient_iteration_upload.html',{
        'form':form,'iterations':CoefficientIteration.objects.filter(owner=request.user)[:15]})


@require_http_methods(['GET','POST'])
def review(request,pk):
    allowed(request)
    with transaction.atomic():
        query=CoefficientIteration.objects.filter(owner=request.user)
        if request.method=='POST':query=query.select_for_update()
        obj=get_object_or_404(query,pk=pk)
        form=MappingForm(obj,request.POST if request.method=='POST' else None)
        if request.method=='POST' and form.is_valid():
            try:
                if form.cleaned_data['revision']!=obj.revision:raise ValidationError('Сопоставление изменилось в другой вкладке. Обновите страницу.')
                action=request.POST.get('action')
                if action not in ('save','confirm'):raise ValidationError('Неизвестное действие.')
                values=form.resolved(complete=action=='confirm')
                if action=='confirm':check_conflicts(obj.payload,values)
                obj.mappings=values;obj.confirmed=action=='confirm';obj.confirmed_at=timezone.now() if obj.confirmed else None
                obj.revision+=1;obj.save(update_fields=['mappings','confirmed','confirmed_at','revision'])
                return redirect('motivation_coefficient_iteration_mapping',pk=obj.pk)
            except ValidationError as exc:form.add_error(None,exc)
    return render(request,'admin_motivation/coefficient_iteration_mapping.html',{
        'iteration':obj,'form':form,'rows':form.rows,'warnings':obj.payload['branches']['warnings']+obj.payload['directors']['warnings']})
