"""Version-independent group/channel exclusion directories."""
from django import forms
from django.contrib import messages
from django.db import transaction
from django.forms import modelformset_factory
from django.shortcuts import render, redirect
from django.views.decorators.http import require_http_methods
from base.access import can_access, motivation_editor_required
from .models import MotivationPlanningGroupExclusion, MotivationChannelExclusion

CONFIG={
    'groups':(MotivationPlanningGroupExclusion,'Исключения групп планов продаж','Группа планов продаж (PlanningGroupSalesERP)'),
    'channels':(MotivationChannelExclusion,'Исключения каналов','Канал (Chanel)'),
}


@require_http_methods(['GET','POST'])
def directory(request,dimension):
    motivation_editor_required(request)
    model,title,label=CONFIG[dimension]
    form_type=forms.modelform_factory(model,fields=('coefficient_type','value','enabled','comment'),
        labels={'value':label},widgets={'value':forms.TextInput(attrs={'class':'form-control'}),
        'coefficient_type':forms.Select(attrs={'class':'form-select'}),'comment':forms.TextInput(attrs={'class':'form-control'})})
    formset_type=modelformset_factory(model,form=form_type,extra=1,max_num=500,validate_max=True)
    formset=formset_type(request.POST if request.method=='POST' else None,queryset=model.objects.all(),prefix='rules')
    if request.method=='POST' and formset.is_valid():
        with transaction.atomic():formset.save()
        messages.success(request,'Справочник сохранён. Правила применяются к плану и факту всех версий.')
        return redirect('motivation_'+dimension+'_exclusion_directory')
    return render(request,'admin_motivation/dimension_exclusion_directory.html',{
        'formset':formset,'title':title,'value_label':label,'dimension':dimension,
        'can_edit':can_access(request.user,'motivation_editor'),'skip_motivation_workflow':True})
