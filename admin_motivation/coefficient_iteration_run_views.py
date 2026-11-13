"""Owner-scoped iteration configuration, calculations and immutable exports."""
from datetime import date
from django import forms
from django.core.exceptions import ValidationError
from django.db.models import Exists,OuterRef
from django.db import IntegrityError
from django.http import FileResponse,JsonResponse,HttpResponse,Http404
from django.shortcuts import get_object_or_404,redirect,render
from django.urls import reverse
from django.views.decorators.http import require_http_methods
from .coefficient_iteration_views import allowed
from .models import CoefficientIteration,CoefficientIterationRun,SalesPlanScenario,SalesPlanLine,GlobalCoeffVersion
from .services.coefficient_iteration_runner import SCENARIOS,start,execute,aggregate_rows
from .services.director_scenario_progress import Progress,token_for


class CalculationForm(forms.Form):
    revision=forms.IntegerField(widget=forms.HiddenInput)
    scenarios=forms.MultipleChoiceField(label='Сценарии расчёта',choices=[(key,value[1]) for key,value in SCENARIOS.items()],
        widget=forms.CheckboxSelectMultiple,initial=list(SCENARIOS))
    plan_2026=forms.ModelChoiceField(label='Загруженный план 2026',queryset=SalesPlanScenario.objects.none(),required=False)
    plan_2027=forms.ModelChoiceField(label='Загруженный план 2027',queryset=SalesPlanScenario.objects.none(),required=False)
    plan_2025=forms.ModelChoiceField(label='Загруженный план 2025',queryset=SalesPlanScenario.objects.none(),required=False)
    formula_version=forms.ModelChoiceField(label='Утверждённая версия формул и поправок директоров для 2025',queryset=GlobalCoeffVersion.objects.none(),required=False)
    quarter_share=forms.DecimalField(label='Квартальная доля, %',initial=80,min_value=0,max_value=100,decimal_places=6)
    year_share=forms.DecimalField(label='Годовая доля, %',initial=20,min_value=0,max_value=100,decimal_places=6)
    payout_threshold=forms.DecimalField(label='Порог выплаты (0,8 = 80%)',initial='.8',min_value=0,max_value=1,decimal_places=8)

    def __init__(self,iteration,*args,**kwargs):
        super().__init__(*args,**kwargs);self.initial['revision']=iteration.revision
        for year in (2025,2026,2027):
            lines=SalesPlanLine.objects.filter(scenario_id=OuterRef('pk'),plan_date__gte=date(year,1,1),plan_date__lt=date(year+1,1,1)).exclude(subdivision='')
            field=self.fields[f'plan_{year}']
            field.queryset=SalesPlanScenario.objects.filter(Exists(lines),is_deleted=False,loaded_at__isnull=False).only('pk','title','source_version','source_type')
            field.label_from_instance=lambda obj:f'{obj.title} · {obj.source_version} · {obj.get_source_type_display()}'
            field.widget.attrs['data-searchable-select']=''
        versions=GlobalCoeffVersion.objects.filter(status='approved',effective_from__lt=date(2027,1,1)).order_by('-effective_from','-pk')
        self.fields['formula_version'].queryset=versions
        if 'formula_version' not in self.initial:self.initial['formula_version']=versions.values_list('pk',flat=True).first()

    def clean(self):
        data=super().clean()
        if data.get('quarter_share') is not None and data.get('year_share') is not None and data['quarter_share']+data['year_share']!=100:
            raise ValidationError('Квартальная и годовая доли должны в сумме составлять 100%.')
        if 'actual_2025' in data.get('scenarios',[]) and not data.get('formula_version'):
            self.add_error('formula_version','Выберите утверждённую версию формул для директоров 2025.')
        for scenario in data.get('scenarios',[]):
            year=SCENARIOS[scenario][0]
            if not data.get(f'plan_{year}'):self.add_error(f'plan_{year}',f'Выберите загруженный план {year}.')
        return data

    def configuration(self):
        result=dict(self.cleaned_data);result.pop('revision',None)
        for name in ('plan_2025','plan_2026','plan_2027','formula_version'):
            result[name]=result[name].pk if result.get(name) else None
        return result


@require_http_methods(['GET','POST'])
def calculations(request,pk):
    allowed(request)
    iteration=get_object_or_404(CoefficientIteration,pk=pk,owner=request.user)
    if request.method=='GET' and request.GET.get('progress'):
        try:response=JsonResponse(Progress(request.GET['progress'],request.user).read())
        except ValidationError as exc:return JsonResponse({'error':' '.join(exc.messages)},status=400)
        response['Cache-Control']='no-store';return response
    run=None
    if request.GET.get('run'):
        try:run_id=int(request.GET['run'])
        except (ValueError,TypeError):raise Http404
        query=CoefficientIterationRun.objects.all()
        if request.method=='GET' and request.GET.get('export')=='xlsx':query=query.defer('inputs','results')
        run=get_object_or_404(query,pk=run_id,iteration=iteration)
    if request.method=='GET' and run and request.GET.get('export')=='xlsx':
        if run.state=='running':return HttpResponse('Расчёт ещё выполняется. Excel будет доступен после завершения.',status=409)
        from .services.coefficient_iteration_excel import workbook
        from .services.coefficient_iteration_export_data import export_snapshot
        return FileResponse(workbook(export_snapshot(run)),as_attachment=True,filename=f'coefficient-iteration-{pk}-run-{run.pk}.xlsx')
    latest=iteration.runs.defer('inputs','results').first()
    initial=(run or latest).configuration if run or latest else {}
    form=CalculationForm(iteration,request.POST if request.method=='POST' else None,initial=initial)
    error='';asynchronous=request.headers.get('Accept')=='application/json'
    if request.method=='POST':
        if not iteration.confirmed:error='Сначала подтвердите сопоставление коэффициентов.'
        elif form.is_valid():
            try:
                progress=Progress(request.POST.get('progress_token',''),request.user);progress.begin()
                from .services.coefficient_iteration_lock import operation
                with operation(iteration.pk):
                    run=start(iteration.pk,request.user,form.configuration(),form.cleaned_data['revision'],recover=True)
                    run=execute(run,progress.update)
                url=reverse('motivation_coefficient_iteration_calculations',args=[pk])+f'?run={run.pk}'
                progress.update('Обработка завершена'+(': есть ошибки' if run.state=='failed' else ''),12,state='completed',url=url)
                if asynchronous:return JsonResponse({'url':url})
                return redirect(url)
            except (ValidationError,IntegrityError) as exc:
                error=' '.join(exc.messages) if isinstance(exc,ValidationError) else 'Итерация уже рассчитывается.'
                if 'progress' in locals():progress.update(error,state='failed')
        else:error=' '.join((form.fields[name].label+': ' if name in form.fields else '')+' '.join(str(message) for message in messages) for name,messages in form.errors.items())
        if asynchronous:return JsonResponse({'error':error,'fields':form.errors,'progress_token':token_for(request.user)},status=400)
    presentation=[]
    if run:
        names={row['branch']:row.get('branch_name',row['branch']) for plan in run.inputs.get('plans',{}).values() for row in plan['rows']}
        for scenario,value in run.results.items():
            qs=[4] if value.get('year')==2026 else [1,2,3,4]
            item={**value,'key':scenario,'quarters':qs}
            if value.get('state')=='completed':
                def present(row):
                    from decimal import Decimal
                    return {**row,'branch_name':names.get(row['branch'],row['branch']),'payments':[row['quarters'][q-1] for q in qs],'quarter_total':sum((Decimal(str(v)) for v in row['quarters']),Decimal(0))}
                item['summary']=[present(row) for row in aggregate_rows(value['rows'])]
                item['recipients']=[present(row) for row in value['rows']]
            presentation.append(item)
    return render(request,'admin_motivation/coefficient_iteration_calculations.html',{
        'iteration':iteration,'form':form,'run':run,'runs':iteration.runs.defer('inputs','results')[:15],
        'presentation':presentation,'error':error,'progress_token':token_for(request.user),'skip_motivation_workflow':True})
