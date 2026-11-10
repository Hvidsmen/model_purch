"""Private five-case director report, separate from live model assignments."""
from datetime import date
import logging
import uuid
from django import forms
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Exists, OuterRef
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods
from base.access import ADMIN, MODELER, DIRECTOR, FINANCIER, role_for
from .reports_2025 import can_view
from .models import SalesPlanScenario, SalesPlanLine, GlobalCoeffVersion, DirectorEmployee, DirectorScenarioComparison
from .services.director_scenarios import calculate
from .services.director_scenario_excel import workbook
from .services.calculation_jobs import failure_message


class ComparisonForm(forms.Form):
    plan_2025=forms.ModelChoiceField(label='Загруженный план 2025',queryset=SalesPlanScenario.objects.none())
    plan_2026=forms.ModelChoiceField(label='Загруженный план 2026',queryset=SalesPlanScenario.objects.none())
    coefficient_version=forms.ModelChoiceField(label='Утверждённая версия модели 2026',queryset=GlobalCoeffVersion.objects.none())
    source_moscow=forms.ModelChoiceField(label='Директор Москвы 2026 / источник ставок Москвы',queryset=DirectorEmployee.objects.none())
    source_tt=forms.ModelChoiceField(label='Директор ТТ 2026 / источник ставок ТТ',queryset=DirectorEmployee.objects.none())
    target_moscow=forms.ModelChoiceField(label='Получатель директорских ставок Москвы в варианте 1 (2025)',queryset=DirectorEmployee.objects.none())
    target_tt=forms.ModelChoiceField(label='Получатель директорских ставок ТТ в варианте 1 (2025)',queryset=DirectorEmployee.objects.none())

    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        for year in (2025,2026):
            lines=SalesPlanLine.objects.filter(scenario_id=OuterRef('pk'),
                plan_date__gte=date(year,1,1),plan_date__lt=date(year+1,1,1)).exclude(subdivision='')
            field=self.fields[f'plan_{year}']
            field.queryset=SalesPlanScenario.objects.filter(Exists(lines),is_deleted=False,loaded_at__isnull=False).only('pk','title','source_version')
            field.label_from_instance=lambda obj:f'{obj.title} · {obj.source_version}'
        self.fields['coefficient_version'].queryset=GlobalCoeffVersion.objects.filter(status='approved',effective_from__lt=date(2027,1,1)).order_by('-effective_from','-pk')
        for key in ('source_moscow','source_tt','target_moscow','target_tt'):
            self.fields[key].widget.attrs.update({'data-searchable-select':'','aria-label':self.fields[key].label})
            self.fields[key].queryset=DirectorEmployee.objects.order_by('full_name','pk')
            self.fields[key].label_from_instance=lambda obj:f'{obj.full_name} · № {obj.pk}'+(' · архив' if obj.is_archived else '')
        for field in self.fields.values():field.error_messages['required']='Обязательное поле.'

    def clean(self):
        data=super().clean();people=[data.get(key) for key in ('source_moscow','source_tt','target_moscow','target_tt')]
        if all(people) and len({p.pk for p in people})!=4:raise forms.ValidationError('Выберите четырёх разных сотрудников.')
        return data


@require_http_methods(['GET','POST'])
def report(request):
    if not can_view(request.user):raise PermissionDenied
    can_calculate=role_for(request.user,'motivation') in (ADMIN,MODELER,DIRECTOR,FINANCIER)
    snapshot=DirectorScenarioComparison.objects.first()
    if 'calculation' in request.GET:
        try:key=int(request.GET['calculation'])
        except (ValueError,TypeError):raise Http404
        snapshot=get_object_or_404(DirectorScenarioComparison,pk=key)
    form=ComparisonForm(request.POST if request.method=='POST' else None,initial=snapshot.configuration if snapshot else {})
    error=''
    if request.method=='POST':
        if not can_calculate:raise PermissionDenied
        if form.is_valid():
            data=form.cleaned_data
            try:
                result=calculate(data['plan_2025'].pk,data['plan_2026'].pk,data['coefficient_version'].pk,
                    *[data[k].pk for k in ('source_moscow','source_tt','target_moscow','target_tt')])
                return redirect(reverse('motivation_report_director_scenarios')+f'?calculation={result.pk}')
            except ValidationError as exc:error=' '.join(exc.messages)
            except Exception as exc:
                reference=uuid.uuid4().hex[:12]
                logging.getLogger(__name__).exception('Director comparison failed [%s]',reference)
                error=failure_message(exc,reference)
    if request.GET.get('export')=='xlsx' and snapshot:
        return FileResponse(workbook(snapshot),as_attachment=True,filename=f'director-scenarios-{snapshot.pk}.xlsx')
    data=snapshot.results if snapshot else None
    cases=[]
    if data:
        for case in data['scenarios']:cases.append({**case,'rows':[r for r in data['rows'] if r['scenario']==case['id']]})
    return render(request,'admin_motivation/reports_next/director_scenarios.html',{
        'form':form,'can_calculate':can_calculate,'snapshot':snapshot,'data':data,'cases':cases,'error':error,
        'history':DirectorScenarioComparison.objects.only('pk','created_at')[:20]})
