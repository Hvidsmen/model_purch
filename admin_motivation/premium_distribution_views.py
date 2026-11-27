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
    is_admin=role_for(request.user,'motivation')==ADMIN
    archived=request.GET.get('archive')=='1'
    batches=PremiumDistribution.objects.select_related('subdivision','execution').filter(state='archived') if archived else PremiumDistribution.objects.select_related('subdivision','execution').filter(state__in=['draft','completed'])
    if request.method=='POST' and request.POST.get('action')=='return_coefficients':
        batch=get_object_or_404(PremiumDistribution,pk=request.POST.get('batch'))
        try:
            from .services.closing_return import return_to_ceo
            return_to_ceo(request.user,batch.pk,int(request.POST.get('revision','0')),int(request.POST.get('coefficient_revision','0')),request.POST.get('reason',''))
            messages.success(request,'Возвращено к коэффициентам. Прежнее распределение сохранено для сравнения и больше не применяется. После подтверждения директор должен распределить премии заново.')
        except (ValidationError,ValueError) as exc:
            messages.error(request,'; '.join(exc.messages) if isinstance(exc,ValidationError) else 'Некорректная версия.')
        return redirect(reverse('motivation_distributions')+'?'+urlencode({'year':batch.year,'period':batch.period})+'#closing-coefficients')
    if request.method=='POST' and request.POST.get('action') in ('delete_draft','archive'):
        return_period={}
        try:
            batch=get_object_or_404(PremiumDistribution,pk=int(request.POST.get('batch','0')))
            return_period={'year':batch.year,'period':batch.period}
            service.retire(request.user,batch.pk,int(request.POST.get('revision','0')),request.POST['action'],request.POST.get('reason',''))
            messages.success(request,'Распределение перенесено в архив и больше не применяется.' if request.POST['action']=='archive' else 'Черновик удалён. Можно создать новое распределение за этот период.')
        except (ValidationError,ValueError) as exc:
            messages.error(request,'; '.join(exc.messages) if isinstance(exc,ValidationError) else 'Некорректная версия.')
        return redirect(reverse('motivation_distributions')+('?' + urlencode(return_period) if return_period else ''))
    if allowed is not None:batches=batches.filter(subdivision__subdivision_key__in=allowed)
    form=StartForm(request.POST if request.method=='POST' and request.POST.get('action','') not in ('confirm_coefficients','confirm_coefficient') else None,user=request.user) if can_edit else None
    can_confirm=role_for(request.user,'motivation') in (ADMIN,DIRECTOR)
    from .services.closing_bulk import workspace
    closing=workspace(request,can_confirm)
    selection=closing['closing_selection']
    selected=selection.cleaned_data if selection.is_valid() else {}
    selected_year=selected.get('year',timezone.localdate().year)
    selected_period=selected.get('period',4)
    if closing['closing_count'] is not None:
        messages.success(request,f"Множители зафиксированы: {closing['closing_count']} подразделений.")
        return redirect(reverse('motivation_distributions')+'?'+urlencode({'year':closing['closing_year'],'period':closing['closing_period'],'branch_filter':request.POST.getlist('branch_filter')},doseq=True)+'#closing-coefficients')
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
                if str(request.POST.get('return_subdivision'))==str(coefficient_form.cleaned_data['subdivision'].pk):
                    target=reverse('coeff_subdivisions_admin_motivation',args=[coefficient_form.cleaned_data['subdivision'].pk])
                    return redirect(target+'?'+urlencode({'version':request.POST.get('return_version',''),'manager_year':coefficient_form.cleaned_data['year']})+'#manager-details')
                return redirect('motivation_distributions')
            except ValidationError as exc:coefficient_form.add_error(None,exc)
    elif request.method=='POST' and request.POST.get('action')!='confirm_coefficients':
        if not can_edit:raise PermissionDenied
        if form.is_valid():
            try:
                from .services.execution_tracking import latest_for_report
                period=form.cleaned_data['period']
                if 'year' in request.POST and int(request.POST['year'])!=selected_year:
                    raise ValidationError('Период изменился. Обновите страницу.')
                from .services.main_quarter_mode import enabled
                variant='main' if enabled() or period in (4,5) else 'all_2026'
                execution=latest_for_report(latest_calculation(variant),variant=variant,persist=True)
                if execution is None or execution.state!='completed':raise ValidationError('Нет завершённого расчёта для этого периода. Сначала запустите расчёт.')
                if 'year' in request.POST and execution.parameters.get('fact_year')!=selected_year:
                    raise ValidationError('Нет завершённого расчёта за выбранный год.')
                batch=service.create(request.user,execution=execution,**form.cleaned_data)
                return redirect('motivation_distribution_detail',pk=batch.pk)
            except (ValidationError,IntegrityError) as exc:
                form.add_error(None,'Распределение уже создано.' if isinstance(exc,IntegrityError) else exc)
    from .services.distribution_workspace import registry
    if role_for(request.user,'motivation')=='Финансист' and not archived:
        batches=batches.filter(state='completed')
    if closing.get('closing_grid'):
        for name,field in closing['closing_grid'].fields.items():
            if name.startswith('coefficient_'):field.widget.attrs['form']='pw-coefficients'
    period_batches=list(batches.filter(year=selected_year,period=selected_period))
    registry_rows,registry_counts=registry(request.user,selected_year,selected_period,period_batches,closing.get('closing_grid'),archived)
    filter_source=request.POST if request.method=='POST' and request.POST.get('action')=='confirm_coefficients' else request.GET
    selected_branches=list(dict.fromkeys(value for value in filter_source.getlist('branch_filter') if value.isdigit()))
    options={str(row['branch'].pk):row['branch'].subdivision_name for row in registry_rows}
    if selected_branches:
        registry_rows=[row for row in registry_rows if str(row['branch'].pk) in selected_branches]
        registry_counts={'waiting':sum(not row['record'] or row['awaiting_confirmation'] for row in registry_rows),
            'working':sum(bool(row['batch'] and row['batch'].state=='draft') for row in registry_rows),
            'completed':sum(bool(row['batch'] and row['batch'].state=='completed') for row in registry_rows)}
    closing.update(branch_filter_options=[{'value':value,'label':label,'selected':value in selected_branches} for value,label in options.items()],
        selected_branches=selected_branches,branch_filter_caption=', '.join(options[value] for value in selected_branches if value in options) if 0<len(selected_branches)<=2 else f'Выбрано: {len(selected_branches)}' if selected_branches else 'Все подразделения',
        branch_filter_query=''.join('&branch_filter='+value for value in selected_branches))
    closing.update(registry_rows=registry_rows,registry_counts=registry_counts,selected_year=selected_year,selected_period=selected_period,skip_motivation_workflow=True)
    return render(request,'admin_motivation/distribution/index.html',{**closing,'can_edit_distributions':can_edit,'is_admin':is_admin,'archived':archived,'coefficient_form':coefficient_form,'coefficients':coefficients[:200],'form':form,'batches':batches[:200],'sources':[(label,latest_calculation(variant)) for variant,label in [('main','Все кварталы и закрытие года')]]})


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
    if batch.state in ('archived','deleted'):sheet.cell(1,9,'АРХИВ: распределение больше не применяется')
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
    from .services.distribution_shared import enabled as shared_enabled,pools
    if shared_enabled(snapshot.get('source',{})):
        funds.cell(1,3,'Исходный фонд');funds.cell(1,4,'После распределения');funds.cell(1,5,'Исходный фонд минус распределено')
        common=book.create_sheet('Общий остаток')
        common.append(['Тип выплаты','Исходная сумма','Распределено','Остаток','Валюта'])
        for pool in pools(snapshot['funds'],snapshot['rows']):common.append([pool['label'],service.cash(pool['amount']),pool['allocated'],pool['remaining'],snapshot['currency']])
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
            if request.POST.get('action')=='upgrade_shared':
                from .services.distribution_shared import upgrade
                upgrade(request.user,pk,int(request.POST.get('revision','0')))
                messages.success(request,'Черновик обновлён: три вкладки, общий остаток по выплатам и лимит уменьшения 25%.')
                return redirect('motivation_distribution_detail',pk=pk)
            if request.POST.get('action')=='begin_stages':
                from .services.distribution_stages import begin_legacy
                begin_legacy(request.user,pk,int(request.POST.get('revision','0')),request.POST.get('closing_coefficient','1'),request.POST.get('transition_reason',''))
                messages.success(request,'Множитель подтверждён. Открыт этап распределения продаж.')
                return redirect('motivation_distribution_detail',pk=pk)
            action=request.POST.get('action','save')
            creating=action=='add_new_person'
            if creating:action='save'
            if action=='add_director':
                if not request.POST.get('new_director'):raise ValidationError('Выберите директора подразделения.')
                action='save'
            if action=='add_person':
                if not request.POST.get('new_employee'):raise ValidationError('Выберите сотрудника для добавления.')
                action='save'
            changes={r['id']:{'amount':request.POST.get('amount_'+r['id'],r['amount'] if batch.source.get('workflow',{}).get('rebalance_policy')=='shared-payments-v1' else ''),'comment':request.POST.get('comment_'+r['id'],''),'weight':request.POST.get('weight_'+r['id'],r.get('weight','0')),'direct_amount':request.POST.get('sales_amounts')=='1'} for r in batch.rows}
            batch=service.update(request.user,pk,int(request.POST.get('revision','0')),changes,action=action,
                addition={'director':request.POST.get('new_director',''),'direct_amount':request.POST.get('sales_amounts')=='1','employee':request.POST.get('new_employee','') if not creating else '', 'create':creating,'full_name':request.POST.get('new_full_name',''),'personnel_number':request.POST.get('new_personnel_number',''),'weight':'0','kind':request.POST.get('new_kind',''),'payment':request.POST.get('new_payment','')},comment=request.POST.get('reason',''))
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
    from .services.distribution_shared import enabled as shared_enabled,pools,minimum
    shared=shared_enabled(snapshot.get('source',{}))
    try:funds=pools(snapshot['funds'],snapshot['rows']) if shared else service.balances(snapshot['funds'],snapshot['rows'])
    except ValidationError:funds=[]
    url=request.build_absolute_uri(reverse('motivation_distribution_detail',args=[pk]))+'?version='+str(revision)
    editable=can_edit and batch.state=='draft' and not version
    workflow=snapshot.get('source',{}).get('workflow') or {}
    stage=workflow.get('stage','legacy')
    visible_kind={'sales':'Продажи','policies':'Политики','directors':'Директора'}.get(stage) if snapshot['state']=='draft' else None
    visible_rows=[r for r in snapshot['rows'] if visible_kind is None or r['kind']==visible_kind]
    if visible_kind and not shared:funds=[f for f in funds if f['kind']==visible_kind]
    employees=service.eligible_employees(batch)
    if stage=='sales':
        payments={f['payment'] for f in snapshot['funds'] if f['kind']=='Продажи'}
        existing={}
        for row in snapshot['rows']:
            if row['kind']=='Продажи' and row.get('employee_id'):existing.setdefault(row['employee_id'],set()).add(row['payment'])
        employees=[employee for employee in employees if not payments.issubset(existing.get(employee.pk,set()))]
    display_rows=[{**r,'edit_amount':editable and ((r['kind']=='Политики' and stage=='policies') or (r['kind']=='Продажи' and stage=='sales') or stage=='legacy'),'edit_weight':editable and r['kind']=='Продажи' and stage=='sales'} for r in visible_rows]
    if shared:
        from decimal import Decimal
        for row in display_rows:
            row['edit_amount']=editable and row['kind']==visible_kind and service.cash(row['original'])>=0
            row['minimum']=minimum(row)
            try:row['reduction']=(1-service.cash(row['amount'])/service.cash(row['original']))*100 if service.cash(row['original'])>0 else None
            except ValidationError:row['reduction']=None
        editable_ids={r['id'] for r in display_rows if r['edit_amount']}
        for fund in funds:
            fund['fixed_allocated']=sum((service.cash(r['amount']) for r in snapshot['rows'] if r['payment']==fund['payment'] and r['id'] not in editable_ids),Decimal(0))
    previous_distribution=snapshot.get('source',{}).get('previous_distribution')
    return render(request,'admin_motivation/distribution/detail.html',{'previous_distribution':previous_distribution,'skip_motivation_workflow':True,'shared_pool':shared,'can_upgrade_shared':editable and bool(workflow) and not shared,'directors':batch.subdivision.directors.filter(is_archived=False),'can_begin_stages':editable and stage=='legacy' and role_for(request.user,'motivation') in (ADMIN,DIRECTOR),'workflow':workflow,'stage':stage,'person_input':{'full_name':request.POST.get('new_full_name',''),'personnel_number':request.POST.get('new_personnel_number',''),'weight':'0','employee':request.POST.get('new_employee',''),'payment':request.POST.get('new_payment','')},'display_rows':display_rows,'batch':batch,'snapshot':snapshot,'revision':revision,'funds':funds,'editable':editable,
        'can_reopen':not version and batch.state=='completed' and role_for(request.user,'motivation') in (ADMIN,DIRECTOR),
        'report_url':url,'mail_url':'mailto:?'+urlencode({'subject':f'Премии: {batch.subdivision} · {batch.year} · {batch.get_period_display()}', 'body':'Распределение премий завершено. Отчёт и Excel: '+url}),
        'employees':employees,'history':batch.events.select_related('actor'),'payments':service.PAYMENTS.items(),'kinds':service.KINDS})
