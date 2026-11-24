from io import BytesIO
from urllib.parse import urlencode
from django import forms
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods
from django.utils import timezone
from base.access import role_for, ADMIN, DIRECTOR, BRANCH_DIRECTOR
from .models import PremiumDistribution, MotivationExecution, Subdivision, PeriodMotivationCoefficient
from .services import premium_distribution as service


class StartForm(forms.Form):
    subdivision=forms.ModelChoiceField(queryset=Subdivision.objects.none(),label='Подразделение')
    period=forms.TypedChoiceField(coerce=int,choices=PremiumDistribution._meta.get_field('period').choices,label='Период',initial=4)
    currency=forms.ChoiceField(choices=[('USD','USD'),('RUB','RUB')],label='Валюта распределения')

    def __init__(self,*args,user,**kwargs):
        super().__init__(*args,**kwargs)
        allowed=service.branches(user,write=True)
        self.fields['subdivision'].queryset=Subdivision.objects.all() if allowed is None else Subdivision.objects.filter(subdivision_key__in=allowed)


class CoefficientForm(forms.Form):
    subdivision=forms.ModelChoiceField(queryset=Subdivision.all_objects.all(),label='Подразделение')
    year=forms.IntegerField(min_value=2000,max_value=2100,label='Год')
    period=forms.TypedChoiceField(coerce=int,choices=PremiumDistribution._meta.get_field('period').choices,label='Период')
    coefficient=forms.DecimalField(min_value=0,max_digits=12,decimal_places=4,initial=1,label='Общий множитель мотивации')
    revision=forms.IntegerField(initial=0,widget=forms.HiddenInput)


def latest_calculation(variant):
    from .services.calculation_variants import main_runs
    runs=main_runs() if variant=='main' else MotivationExecution.objects.filter(parameters__variant=variant)
    return runs.filter(state='completed').order_by('-started_at','-pk').first()


@require_http_methods(['GET','POST'])
def index(request):
    allowed=service.branches(request.user)
    can_edit=role_for(request.user,'motivation') in (ADMIN,DIRECTOR,BRANCH_DIRECTOR)
    batches=PremiumDistribution.objects.select_related('subdivision','execution')
    if allowed is not None:batches=batches.filter(subdivision__subdivision_key__in=allowed)
    form=StartForm(request.POST or None,user=request.user) if can_edit else None
    can_confirm=role_for(request.user,'motivation') in (ADMIN,DIRECTOR)
    coefficients=PeriodMotivationCoefficient.objects.select_related('subdivision','confirmed_by').order_by('-year','-period')
    if allowed is not None:coefficients=coefficients.filter(subdivision__subdivision_key__in=allowed)
    initial={'year':timezone.localdate().year,'coefficient':1,'revision':0}
    if request.GET.get('coefficient') and can_confirm:
        selected=get_object_or_404(coefficients,pk=request.GET['coefficient'])
        initial={key:getattr(selected,key) for key in ('year','period','coefficient','revision')};initial['subdivision']=selected.subdivision_id
    confirming=request.method=='POST' and request.POST.get('action')=='confirm_coefficient'
    coefficient_form=CoefficientForm(request.POST if confirming else None,initial=initial) if can_confirm else None
    if confirming:
        if not can_confirm:raise PermissionDenied
        if coefficient_form.is_valid():
            try:
                from .services.closing_coefficient import confirm
                confirm(request.user,**coefficient_form.cleaned_data)
                messages.success(request,'Этап 1 завершён. Множитель применяется в отчётах и при фиксации фондов один раз.')
                return redirect('motivation_distributions')
            except ValidationError as exc:coefficient_form.add_error(None,exc)
    elif request.method=='POST':
        if not can_edit:raise PermissionDenied
        if form.is_valid():
            try:
                from .services.execution_tracking import latest_for_report
                period=form.cleaned_data['period']
                from .services.main_quarter_mode import enabled
                variant='main' if enabled() or period in (4,5) else 'all_2026'
                execution=latest_for_report(latest_calculation(variant),variant=variant,persist=True)
                if execution is None or execution.state!='completed':raise ValidationError('Нет завершённого расчёта для этого периода. Сначала запустите расчёт.')
                batch=service.create(request.user,execution=execution,**form.cleaned_data)
                return redirect('motivation_distribution_detail',pk=batch.pk)
            except (ValidationError,IntegrityError) as exc:
                form.add_error(None,'Распределение уже создано.' if isinstance(exc,IntegrityError) else exc)
    return render(request,'admin_motivation/distribution/index.html',{'coefficient_form':coefficient_form,'coefficients':coefficients[:200],'form':form,'batches':batches[:200],'sources':[(label,latest_calculation(variant)) for variant,label in [('main','Все кварталы и закрытие года')]]})


def snapshot_for(batch,user,version):
    service.authorize(user,batch.subdivision)
    if version:
        e=get_object_or_404(batch.events,revision=version,action='complete')
        return e.snapshot,e.revision
    return {'rows':batch.rows,'funds':batch.funds,'state':batch.state,'currency':batch.currency,'source':batch.source},batch.revision


def export(request,batch,snapshot,revision):
    from openpyxl import Workbook
    from openpyxl.styles import Font,PatternFill
    from openpyxl.utils import get_column_letter
    if snapshot['state']!='completed':raise PermissionDenied('Excel доступен после завершения распределения.')
    book=Workbook(); sheet=book.active;sheet.title='Распределение премий'
    url=request.build_absolute_uri(reverse('motivation_distribution_detail',args=[batch.pk]))+'?version='+str(revision)
    def text(value):
        value=str(value)
        return "'"+value if value.lstrip().startswith(('=','+','-','@')) else value
    sheet.append(['Распределение премий',batch.year,batch.get_period_display(),batch.subdivision.subdivision_key,snapshot['currency'],'Версия',revision])
    sheet.append(['Ссылка на зафиксированный отчёт',url])
    sheet.append(['Расчёт',snapshot['source']['run_id'],'Общий множитель',snapshot['source'].get('workflow',{}).get('coefficient','1')])
    headers=['Подразделение','Год','Период','Вид мотивации','Тип выплаты','Сотрудник','По расчёту','После распределения','Изменение','Валюта','Комментарий','Вес продаж']
    sheet.append(headers)
    for row in snapshot['rows']:
        a=service.cash(row['original']); b=service.cash(row['amount'])
        sheet.append([text(batch.subdivision.subdivision_key),batch.year,batch.get_period_display(),row['kind'],service.PAYMENTS[row['payment']],text(row['author'] or 'Не распределено'),a,b,b-a,snapshot['currency'],text(row['comment']),row.get('weight','')])
    total_row=sheet.max_row+1
    sheet.append(['Итого','','','','','',sum(service.cash(r['original']) for r in snapshot['rows']),sum(service.cash(r['amount']) for r in snapshot['rows'])])
    for row in sheet.iter_rows(min_row=5,max_row=total_row):
        for cell in row[6:9]:cell.number_format='#,##0.00'
    for cell in sheet[4]:cell.font=Font(bold=True,color='FFFFFF');cell.fill=PatternFill('solid',fgColor='6551AE')
    sheet.freeze_panes='G5';sheet.auto_filter.ref=f'A4:L{total_row-1}'
    for i in range(1,13):sheet.column_dimensions[get_column_letter(i)].width=32 if i in (1,6,11) else 22
    funds=book.create_sheet('Фонды')
    funds.append(['Вид мотивации','Тип выплаты','Фонд','Распределено','Остаток','Валюта'])
    for f in service.balances(snapshot['funds'],snapshot['rows']):funds.append([f['kind'],f['label'],service.cash(f['amount']),f['allocated'],f['remaining'],snapshot['currency']])
    output=BytesIO();book.save(output);output.seek(0)
    return FileResponse(output,as_attachment=True,filename=f'premiums-{batch.year}-{batch.period}-v{revision}.xlsx')


@require_http_methods(['GET','POST'])
def detail(request,pk):
    batch=get_object_or_404(PremiumDistribution.objects.select_related('subdivision','execution'),pk=pk)
    service.authorize(request.user,batch.subdivision)
    can_edit=role_for(request.user,'motivation') in (ADMIN,DIRECTOR,BRANCH_DIRECTOR)
    if request.method=='POST':
        try:
            if request.POST.get('action')=='begin_stages':
                from .services.distribution_stages import begin_legacy
                begin_legacy(request.user,pk,int(request.POST.get('revision','0')),request.POST.get('closing_coefficient','1'),request.POST.get('transition_reason',''))
                messages.success(request,'Множитель подтверждён. Открыт этап распределения продаж по весам.')
                return redirect('motivation_distribution_detail',pk=pk)
            changes={r['id']:{'amount':request.POST.get('amount_'+r['id'],''),'comment':request.POST.get('comment_'+r['id'],''),'weight':request.POST.get('weight_'+r['id'],r.get('weight','0'))} for r in batch.rows}
            batch=service.update(request.user,pk,int(request.POST.get('revision','0')),changes,action=request.POST.get('action','save'),
                addition={'employee':request.POST.get('new_employee',''),'kind':request.POST.get('new_kind',''),'payment':request.POST.get('new_payment','')},comment=request.POST.get('reason',''))
            messages.success(request,'Распределение завершено. Excel и ссылка готовы для передачи финансистам.' if batch.state=='completed' else 'Изменения сохранены.')
            return redirect('motivation_distribution_detail',pk=pk)
        except (ValidationError,ValueError) as exc:
            messages.error(request,'; '.join(exc.messages) if isinstance(exc,ValidationError) else 'Некорректная версия.')
            # Preserve entered values so an unsuccessful completion never loses work.
            batch.refresh_from_db()
            if batch.state=='draft':
                batch.rows=[{**r,'amount':request.POST.get('amount_'+r['id'],r['amount']),'comment':request.POST.get('comment_'+r['id'],r['comment']),'weight':request.POST.get('weight_'+r['id'],r.get('weight','0'))} for r in batch.rows]
    try:version=int(request.GET['version']) if request.GET.get('version') else None
    except ValueError:raise Http404
    snapshot,revision=snapshot_for(batch,request.user,version)
    if request.GET.get('export')=='xlsx':return export(request,batch,snapshot,revision)
    try:funds=service.balances(snapshot['funds'],snapshot['rows'])
    except ValidationError:funds=[]
    url=request.build_absolute_uri(reverse('motivation_distribution_detail',args=[pk]))+'?version='+str(revision)
    editable=can_edit and batch.state=='draft' and not version
    workflow=snapshot.get('source',{}).get('workflow') or {}
    stage=workflow.get('stage','legacy')
    display_rows=[{**r,'edit_amount':editable and r['kind']=='Политики' and stage=='policies' or editable and stage=='legacy','edit_weight':editable and r['kind']=='Продажи' and stage=='sales'} for r in snapshot['rows']]
    return render(request,'admin_motivation/distribution/detail.html',{'can_begin_stages':editable and stage=='legacy' and role_for(request.user,'motivation') in (ADMIN,DIRECTOR),'workflow':workflow,'stage':stage,'display_rows':display_rows,'batch':batch,'snapshot':snapshot,'revision':revision,'funds':funds,'editable':editable,
        'can_reopen':not version and batch.state=='completed' and role_for(request.user,'motivation') in (ADMIN,DIRECTOR),
        'report_url':url,'mail_url':'mailto:?'+urlencode({'subject':f'Премии: {batch.subdivision} · {batch.year} · {batch.get_period_display()}', 'body':'Распределение премий завершено. Отчёт и Excel: '+url}),
        'employees':service.eligible_employees(batch),'history':batch.events.select_related('actor'),'payments':service.PAYMENTS.items(),'kinds':service.KINDS})
