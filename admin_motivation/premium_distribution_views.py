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
from base.access import role_for, ADMIN, DIRECTOR, BRANCH_DIRECTOR
from .models import PremiumDistribution, MotivationExecution, Subdivision
from .services import premium_distribution as service


class StartForm(forms.Form):
    execution=forms.ModelChoiceField(queryset=MotivationExecution.objects.none(),label='Завершённый расчёт')
    subdivision=forms.ModelChoiceField(queryset=Subdivision.objects.none(),label='Подразделение')
    period=forms.TypedChoiceField(coerce=int,choices=PremiumDistribution._meta.get_field('period').choices,label='Период')
    currency=forms.ChoiceField(choices=[('USD','USD'),('RUB','RUB')],label='Валюта распределения')

    def __init__(self,*args,user,**kwargs):
        super().__init__(*args,**kwargs)
        self.fields['execution'].queryset=MotivationExecution.objects.filter(state='completed').order_by('-completed_at')
        self.fields['execution'].label_from_instance=lambda e:f"{e.parameters.get('fact_year')} · {e.parameters.get('variant','main')} · {e.completed_at:%d.%m.%Y %H:%M}" if e.completed_at else str(e.run_id)
        allowed=service.branches(user,write=True)
        self.fields['subdivision'].queryset=Subdivision.objects.all() if allowed is None else Subdivision.objects.filter(subdivision_key__in=allowed)


@require_http_methods(['GET','POST'])
def index(request):
    allowed=service.branches(request.user)
    can_edit=role_for(request.user,'motivation') in (ADMIN,DIRECTOR,BRANCH_DIRECTOR)
    batches=PremiumDistribution.objects.select_related('subdivision','execution')
    if allowed is not None:batches=batches.filter(subdivision__subdivision_key__in=allowed)
    form=StartForm(request.POST or None,user=request.user) if can_edit else None
    if request.method=='POST':
        if not can_edit:raise PermissionDenied
        if form.is_valid():
            try:
                batch=service.create(request.user,**form.cleaned_data)
                return redirect('motivation_distribution_detail',pk=batch.pk)
            except (ValidationError,IntegrityError) as exc:
                form.add_error(None,'Распределение уже создано.' if isinstance(exc,IntegrityError) else exc)
    return render(request,'admin_motivation/distribution/index.html',{'form':form,'batches':batches[:200]})


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
    sheet.append(['Расчёт',snapshot['source']['run_id']])
    headers=['Подразделение','Год','Период','Вид мотивации','Тип выплаты','Сотрудник','По расчёту','После распределения','Изменение','Валюта','Комментарий']
    sheet.append(headers)
    for row in snapshot['rows']:
        a=service.cash(row['original']); b=service.cash(row['amount'])
        sheet.append([text(batch.subdivision.subdivision_key),batch.year,batch.get_period_display(),row['kind'],service.PAYMENTS[row['payment']],text(row['author'] or 'Не распределено'),a,b,b-a,snapshot['currency'],text(row['comment'])])
    total_row=sheet.max_row+1
    sheet.append(['Итого','','','','','',sum(service.cash(r['original']) for r in snapshot['rows']),sum(service.cash(r['amount']) for r in snapshot['rows'])])
    for row in sheet.iter_rows(min_row=5,max_row=total_row):
        for cell in row[6:9]:cell.number_format='#,##0.00'
    for cell in sheet[4]:cell.font=Font(bold=True,color='FFFFFF');cell.fill=PatternFill('solid',fgColor='6551AE')
    sheet.freeze_panes='G5';sheet.auto_filter.ref=f'A4:K{total_row-1}'
    for i in range(1,12):sheet.column_dimensions[get_column_letter(i)].width=32 if i in (1,6,11) else 22
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
            changes={r['id']:{'amount':request.POST.get('amount_'+r['id'],''),'comment':request.POST.get('comment_'+r['id'],'')} for r in batch.rows}
            batch=service.update(request.user,pk,int(request.POST.get('revision','0')),changes,action=request.POST.get('action','save'),
                addition={'employee':request.POST.get('new_employee',''),'kind':request.POST.get('new_kind',''),'payment':request.POST.get('new_payment','')},comment=request.POST.get('reason',''))
            messages.success(request,'Распределение завершено. Excel и ссылка готовы для передачи финансистам.' if batch.state=='completed' else 'Изменения сохранены.')
            return redirect('motivation_distribution_detail',pk=pk)
        except (ValidationError,ValueError) as exc:
            messages.error(request,'; '.join(exc.messages) if isinstance(exc,ValidationError) else 'Некорректная версия.')
            # Preserve entered values so an unsuccessful completion never loses work.
            batch.refresh_from_db()
            if batch.state=='draft':
                batch.rows=[{**r,'amount':request.POST.get('amount_'+r['id'],r['amount']),'comment':request.POST.get('comment_'+r['id'],r['comment'])} for r in batch.rows]
    try:version=int(request.GET['version']) if request.GET.get('version') else None
    except ValueError:raise Http404
    snapshot,revision=snapshot_for(batch,request.user,version)
    if request.GET.get('export')=='xlsx':return export(request,batch,snapshot,revision)
    try:funds=service.balances(snapshot['funds'],snapshot['rows'])
    except ValidationError:funds=[]
    url=request.build_absolute_uri(reverse('motivation_distribution_detail',args=[pk]))+'?version='+str(revision)
    editable=can_edit and batch.state=='draft' and not version
    return render(request,'admin_motivation/distribution/detail.html',{'batch':batch,'snapshot':snapshot,'revision':revision,'funds':funds,'editable':editable,
        'can_reopen':not version and batch.state=='completed' and role_for(request.user,'motivation') in (ADMIN,DIRECTOR),
        'report_url':url,'mail_url':'mailto:?'+urlencode({'subject':f'Премии: {batch.subdivision} · {batch.year} · {batch.get_period_display()}', 'body':'Распределение премий завершено. Отчёт и Excel: '+url}),
        'employees':service.eligible_employees(batch),'history':batch.events.select_related('actor'),'payments':service.PAYMENTS.items(),'kinds':service.KINDS})
