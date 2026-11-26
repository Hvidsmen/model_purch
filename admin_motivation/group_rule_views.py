from decimal import Decimal
from django import forms
from django.core.exceptions import PermissionDenied,ValidationError
from django.contrib import messages
from django.shortcuts import get_object_or_404,redirect,render
from django.urls import reverse
from django.http import Http404
from django.views.decorators.http import require_http_methods
from base.access import can_access,role_for,ADMIN,MODELER,DIRECTOR,FINANCIER,MOTIVATION_VIEWER
from .models import GlobalCoeffVersion,Subdivision,Goods
from .services import planning_group_rules as rules


class RuleForm(forms.Form):
    subdivision=forms.ChoiceField(label='Подразделение',required=False)
    kind=forms.ChoiceField(label='Вид коэффициента',choices=[*[(v,v) for v in rules.KINDS],('all','Все виды (политики, продажи, директора, кураторы)')])
    planning=forms.CharField(label='Группа планов продаж',max_length=255,widget=forms.TextInput(attrs={'list':'planning-groups'}))
    formula=forms.ChoiceField(label='Формула для всех товаров группы',required=False,initial='Группа планов продаж',choices=[('','Не переопределять формулы товаров'),*[(v,v) for v in rules.FORMULAS]])
    missing=forms.ChoiceField(label='При отсутствии точной ставки',choices=rules.METHODS)
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.fields['subdivision'].choices=[('','Все подразделения'),*[(v,v) for v in Subdivision.objects.order_by('subdivision_key').values_list('subdivision_key',flat=True).distinct()]]
        for i in range(5):self.fields['k'+str(i)]=forms.DecimalField(label='Резервный K'+str(i)+', %',required=False,min_value=0,max_value=100,max_digits=18,decimal_places=10)
    def clean(self):
        data=super().clean()
        if data.get('missing')=='fixed':
            for i in range(5):
                if data.get('k'+str(i)) is None:self.add_error('k'+str(i),'Заполните резервную ставку.')
        return data


@require_http_methods(['GET','POST'])
def workspace(request):
    if role_for(request.user,'motivation') not in (ADMIN,MODELER,DIRECTOR,FINANCIER,MOTIVATION_VIEWER):raise PermissionDenied
    from .coverage_views import versions_for
    versions=versions_for(request.user)
    version_id=request.POST.get('version') if request.method=='POST' else request.GET.get('version')
    version=get_object_or_404(versions,pk=version_id) if version_id else versions.first()
    if not version:raise Http404('Создайте версию коэффициентов.')
    initial={key:request.GET.get(key,'') for key in ('subdivision','kind','planning')}
    existing=next((r for r in version.planning_rules if all(r.get(k,'')==v for k,v in initial.items())),None)
    if existing:
        initial={**existing,**{'k'+str(i):Decimal(str(existing.get('k'+str(i),0)))*100 for i in range(5)}}
    form=RuleForm(request.POST if request.method=='POST' else None,initial=initial or None)
    if request.method=='POST':
        if not can_access(request.user,'motivation_editor'):raise PermissionDenied
        if form.is_valid():
            data=form.cleaned_data;row={k:data[k] for k in ('subdivision','kind','planning','formula','missing')}
            if row['missing']=='fixed':row.update({'k'+str(i):str(data['k'+str(i)]/100) for i in range(5)})
            try:
                rules.save(request.user,version.pk,request.POST.get('revision'),row,delete=request.POST.get('action')=='delete')
                messages.success(request,'Правило сохранено. Для применения утвердите версию и выполните новую выгрузку и расчёт.')
                return redirect(reverse('motivation_planning_group_rules')+'?version='+str(version.pk))
            except ValidationError as error:form.add_error(None,error)
    return render(request,'admin_motivation/planning_group_rules.html',dict(selected_version=version,versions=versions,form=form,
        editable=version.is_editable and can_access(request.user,'motivation_editor'),rules=version.planning_rules,reserve_fields=[form['k'+str(i)] for i in range(5)],
        planning_groups=Goods.objects.order_by('planning_group_sales').values_list('planning_group_sales',flat=True).distinct()))
