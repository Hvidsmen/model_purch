"""Separate saved 2026 plan-budget calculation, without SQL Agent or fact runs."""
from datetime import date
import logging
from django import forms
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import OperationalError
from django.db.models import Exists, OuterRef
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods
from base.access import ADMIN, MODELER, DIRECTOR, FINANCIER, role_for
from .reports_2025 import can_view
from .models import SalesPlanScenario, SalesPlanLine, GlobalCoeffVersion, IntegralPlan2026Calculation
from .services.integral_plan_2026 import calculate, workbook


class CalculationForm(forms.Form):
    scenario = forms.ModelChoiceField(label='Загруженный план 2026',queryset=SalesPlanScenario.objects.none())
    coefficient_version = forms.ModelChoiceField(label='Утверждённая версия коэффициентов подразделений',queryset=GlobalCoeffVersion.objects.none())

    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        # Probe the existing (scenario, plan_date, ...) index and stop at the first
        # matching row. IN over all year rows scans every loaded plan on opening.
        lines=SalesPlanLine.objects.filter(scenario_id=OuterRef('pk'),
            plan_date__gte=date(2026,1,1),plan_date__lt=date(2027,1,1)).exclude(subdivision='')
        self.fields['scenario'].label_from_instance=lambda obj: f'{obj.title} · {obj.source_version}'
        self.fields['scenario'].queryset=SalesPlanScenario.objects.filter(Exists(lines),is_deleted=False,loaded_at__isnull=False)
        self.fields['scenario'].queryset=self.fields['scenario'].queryset.only('pk','title','source_version')
        self.fields['coefficient_version'].queryset=GlobalCoeffVersion.objects.filter(status='approved',effective_from__lt=date(2027,1,1)).order_by('-effective_from','-pk')
        for field in self.fields.values():
            field.error_messages['required']='Обязательное поле.'
            field.error_messages['invalid_choice']='Выберите доступное значение из списка.'


@require_http_methods(['GET','POST'])
def report(request):
    if not can_view(request.user):raise PermissionDenied
    can_calculate=role_for(request.user,'motivation') in (ADMIN,MODELER,DIRECTOR,FINANCIER)
    snapshot=IntegralPlan2026Calculation.objects.select_related('scenario','coefficient_version').first()
    selected=request.GET.get('calculation')
    if selected is not None:
        try:selected=int(selected)
        except (ValueError,TypeError):raise Http404('Расчёт не найден.')
        snapshot=get_object_or_404(IntegralPlan2026Calculation,pk=selected)
    initial={'scenario':snapshot.scenario_id,'coefficient_version':snapshot.coefficient_version_id} if snapshot else {}
    if not snapshot:
        latest=GlobalCoeffVersion.objects.filter(status='approved',effective_from__lt=date(2027,1,1)).order_by('-effective_from','-pk').first()
        if latest:initial['coefficient_version']=latest.pk
    form=CalculationForm(request.POST if request.method=='POST' else None,initial=initial)
    error=''
    if request.method=='POST':
        if not can_calculate:raise PermissionDenied
        if form.is_valid():
            try:
                result=calculate(form.cleaned_data['scenario'].pk,form.cleaned_data['coefficient_version'].pk)
                return redirect(reverse('motivation_report_integral_2026')+f'?calculation={result.pk}')
            except ValidationError as exc:error=' '.join(exc.messages)
            except OperationalError:
                logging.getLogger(__name__).exception('Integral 2026 plan busy')
                error='План или коэффициенты сейчас обновляются. Повторите расчёт после завершения обновления.'
    if request.GET.get('export')=='xlsx' and snapshot:
        return FileResponse(workbook(snapshot),as_attachment=True,filename='integral-coefficients-plan-2026.xlsx')
    return render(request,'admin_motivation/reports_next/integral_2026.html',{
        'form':form,'can_calculate':can_calculate,'snapshot':snapshot,
        'data':snapshot.results if snapshot else None,'error':error,
        'history':IntegralPlan2026Calculation.objects.only('pk','created_at').order_by('-pk')[:20]})
