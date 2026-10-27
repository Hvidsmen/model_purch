import logging
from zoneinfo import ZoneInfo
from django import forms
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.core import signing
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.http import JsonResponse
from django.views.decorators.cache import never_cache
from django.utils import timezone
from django.views.decorators.http import require_http_methods
from base.access import can_access, role_for, MOTIVATION_VIEWER, FINANCIER
from .models import Subdivision, GlobalCoeffVersion, MotivationCalculationSchedule, SubdivisionMergeLog, SalesPlanScenario
from .services import calculation_jobs, subdivision_merge

logger = logging.getLogger(__name__)


class JobForm(forms.ModelForm):
    payout_threshold = forms.DecimalField(label='Порог выполнения плана для выплаты', min_value=0, max_value=1, max_digits=9, decimal_places=8, initial='0.8', help_text='Коэффициент: 0,8 = 80%. Это порог выплаты, а не квартальная доля.')
    fact_year = forms.IntegerField(label='Год факта', min_value=1, max_value=9999)
    plan_version = forms.ChoiceField(label='Загруженная версия плана продаж')
    quarter_share = forms.DecimalField(label='Квартальная доля, %', min_value=0, max_value=100, max_digits=9, decimal_places=6, initial=80)
    year_share = forms.DecimalField(label='Годовая доля, %', min_value=0, max_value=100, max_digits=9, decimal_places=6, initial=20)
    run_at = forms.DateTimeField(label='Дата и время (Москва)', required=False,
                                 widget=forms.DateTimeInput(attrs={'type': 'datetime-local'}, format='%Y-%m-%dT%H:%M'))
    class Meta:
        model = MotivationCalculationSchedule
        fields = ['name', 'fact_year', 'plan_version', 'quarter_share', 'year_share', 'payout_threshold', 'mode', 'run_at', 'enabled']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        versions = {}
        for plan in SalesPlanScenario.objects.filter(is_deleted=False, loaded_at__isnull=False).exclude(source_version='').order_by('title', 'pk'):
            versions.setdefault(plan.source_version, []).append(plan.title)
        self.fields['plan_version'].choices = [('', 'Выберите загруженную версию плана')] + [
            (version, f"{' / '.join(titles)} · {version}") for version, titles in versions.items()]
        if not versions:
            self.fields['plan_version'].help_text = 'Сначала загрузите план в разделе «Планы продаж».'
        elif self.instance.plan_version and self.instance.plan_version not in versions:
            self.fields['plan_version'].help_text = 'Сохранённой версии нет среди загруженных планов. Загрузите её или выберите другую.'

    def clean_run_at(self):
        value = self.cleaned_data['run_at']
        if value:
            return value.replace(tzinfo=ZoneInfo('Europe/Moscow'))
        return value

    def clean(self):
        values = super().clean()
        if values.get('enabled') and values.get('mode') != 'manual' and not values.get('run_at'):
            self.add_error('run_at', 'Укажите дату и время запуска.')
        if values.get('enabled') and values.get('mode') == 'once' and values.get('run_at') and values['run_at'] <= timezone.now():
            self.add_error('run_at', 'Однократный запуск должен быть в будущем.')
        if values.get('quarter_share') is not None and values.get('year_share') is not None and values['quarter_share'] + values['year_share'] != 100:
            self.add_error(None, 'Квартальная и годовая доли должны в сумме составлять 100%.')
        return values


def admin_required(request):
    if not can_access(request.user, 'motivation_admin') and not (role_for(request.user, 'motivation') in (MOTIVATION_VIEWER, FINANCIER) and request.method in ('GET','HEAD','OPTIONS')):
        raise PermissionDenied


@require_http_methods(['GET', 'POST'])
def calculations(request):
    admin_required(request)
    form = JobForm()
    edited_job_id = None
    if request.method == 'POST':
        job = get_object_or_404(MotivationCalculationSchedule, pk=request.POST['job']) if request.POST.get('job') else None
        edited_job_id = job.pk if job else None
        try:
            if request.POST.get('action') == 'run' and job:
                calculation_jobs.start_job(job)
                job.synced, job.error = True, ''
                job.save(update_fields=['synced', 'error'])
                messages.success(request, 'Запуск задания принят сервером расчёта. Дождитесь статуса «Завершено», затем откройте результат. Статус обновляется автоматически.')
                return redirect('motivation_calculations')
            form = JobForm(request.POST, instance=job, auto_id=f'job_{job.pk}_%s' if job else 'id_%s')
            if form.is_valid():
                job = form.save(commit=False)
                calculation_jobs.sync_job(job)
                job.synced, job.error = True, ''
                job.save()
                messages.success(request, 'Параметры задания сохранены и переданы серверу расчёта. Для ручного расчёта нажмите «Запустить сейчас»; для автоматического проверьте расписание.')
                return redirect('motivation_calculations')
        except Exception as error:
            text = ' '.join(error.messages) if isinstance(error, ValidationError) else 'Не удалось настроить или запустить SQL Agent. Проверьте DWH_SQL_CONN_STR, службу Agent и права на создание заданий и процедур; подробности в журнале.'
            logger.exception('Motivation calculation job operation failed')
            if job and job.pk:
                job.synced, job.error = False, text
                job.save(update_fields=['synced', 'error'])
            messages.error(request, text)
    jobs = list(MotivationCalculationSchedule.objects.order_by('pk'))
    approved = GlobalCoeffVersion.objects.filter(status='approved').order_by('-effective_from', '-pk').first()
    coverage_cache = {}
    for job in jobs:
        job.coverage_version = approved
        plan = SalesPlanScenario.objects.filter(is_deleted=False, loaded_at__isnull=False, source_version=job.plan_version).order_by('-loaded_at', '-pk').first()
        job.coverage_warning = None
        if approved and plan:
            from .services.coefficient_coverage import plan_warning
            if plan.pk not in coverage_cache:
                coverage_cache[plan.pk] = plan_warning(approved, plan)
            job.coverage_warning = coverage_cache[plan.pk]
        job.edit_form = form if edited_job_id == job.pk and form.is_bound else JobForm(instance=job, auto_id=f'job_{job.pk}_%s')
        job.edit_open = bool(edited_job_id == job.pk)
        if job.run_at:
            job.edit_form.initial['run_at'] = job.run_at.astimezone(ZoneInfo('Europe/Moscow')).replace(tzinfo=None)
        from datetime import timedelta
        now=timezone.now().astimezone(ZoneInfo('Europe/Moscow'))
        if job.mode=='manual' or not job.enabled or not job.run_at:
            job.next_run_label='Только вручную' if job.mode=='manual' else 'Автозапуск отключён'
        elif job.mode=='daily':
            next_run=now.replace(hour=job.run_at.astimezone(ZoneInfo('Europe/Moscow')).hour,minute=job.run_at.astimezone(ZoneInfo('Europe/Moscow')).minute,second=job.run_at.second,microsecond=0)
            if next_run<=now:next_run+=timedelta(days=1)
            if next_run<job.run_at:next_run=job.run_at.astimezone(ZoneInfo('Europe/Moscow'))
            job.next_run_label=next_run.strftime('%d.%m %H:%M')+' · по расписанию'
        else:
            job.next_run_label=job.run_at.astimezone(ZoneInfo('Europe/Moscow')).strftime('%d.%m.%Y %H:%M') if job.run_at>timezone.now() else 'Время прошло · проверьте историю'
        job.current_status = 'Проверка статуса…' if job.synced else 'Не синхронизировано'
    return render(request, 'admin_motivation/calculations.html', {'form': JobForm() if edited_job_id else form, 'jobs': jobs, 'report_subdivisions':Subdivision.objects.all(),'report_subdivision':request.GET.get('subdivision','')})


@never_cache
@require_http_methods(['GET'])
def calculation_monitor(request, job_id):
    admin_required(request)
    job = get_object_or_404(MotivationCalculationSchedule, pk=job_id)
    try:
        data = calculation_jobs.monitor(job)
    except Exception:
        logger.exception('SQL Agent monitoring failed for job %s', job.pk)
        return JsonResponse({'error': 'SQL Server недоступен. Автообновление повторит запрос.'}, status=503)
    from .services.execution_tracking import observe
    from .services.workflow import context as workflow_context
    from urllib.parse import urlencode
    execution = observe(job,persist=False) if role_for(request.user, 'motivation')==MOTIVATION_VIEWER else observe(job)
    state=workflow_context()
    data['workflow']={key:state[key] for key in ('export_current','calculated_current','export_label','calculation_label')}
    if data.get('status') in {'Выполняется','Ожидает запуска','Ошибка'}:
        data['workflow'].update(calculated_current=False,calculation_label=data['status'])
    if execution and execution.state == 'completed' and data.get('status') == 'Завершено':
        params = execution.parameters
        data['report_url'] = reverse('motivation_report',args=['manager-detail']) + '?' + urlencode({'year':params['fact_year'],'job':job.pk,'run':'1',**({'execution':execution.pk} if execution.pk else {})})
        data['execution'] = {'completed':execution.completed_at.astimezone(ZoneInfo('Europe/Moscow')).strftime('%d.%m.%Y %H:%M:%S') if execution.completed_at else '',
            'year':params['fact_year'],'plan':params['plan_version'],
            'stale': not (state['export_current'] and state['exported'] and state['exported'].fingerprint==execution.export_snapshot.get('fingerprint'))}
    else:
        data['report_url'] = None
    data['checked_at'] = timezone.now().astimezone(ZoneInfo('Europe/Moscow')).strftime('%H:%M:%S')
    return JsonResponse(data)


@require_http_methods(['GET', 'POST'])
def merge_subdivisions(request):
    admin_required(request)
    data = request.POST if request.method == 'POST' else request.GET
    info, errors, preview_token = None, [], None
    source_ids = sorted(set(data.getlist('source')))
    priority_source = data.get('priority_source') or (source_ids[0] if len(source_ids) == 1 else '')
    selected = data.get('version') or GlobalCoeffVersion.objects.exclude(status='superseded').order_by('-pk').values_list('pk', flat=True).first()

    def selection():
        return [source_ids, str(data.get('target')), str(selected), data.get('policy'), priority_source if data.get('policy') == 'source' else '']

    if request.method == 'POST':
        try:
            if data.get('action') == 'sync_sql':
                calculation_jobs.sync_merge_configuration()
                messages.success(request, 'Объединение и утверждённые коэффициенты синхронизированы с SQL Server. Запускайте расчёт из административного раздела портала.')
                return redirect('motivation_subdivision_merge')
            info = subdivision_merge.preview_many(source_ids, data.get('target'), selected)
            if data.get('action') == 'apply':
                if data.get('confirm') != 'yes':
                    raise ValidationError('Подтвердите объединение после проверки предварительного результата.')
                try:
                    reviewed = signing.loads(data.get('preview_token', ''), salt='subdivision-merge-review', max_age=1800)
                except signing.BadSignature:
                    raise ValidationError('Сначала проверьте объединение. Предварительный просмотр действует 30 минут.')
                if reviewed['selection'] != selection():
                    raise ValidationError('Параметры изменились после проверки. Нажмите «Проверить объединение» ещё раз.')
                logs = subdivision_merge.apply_many(source_ids, data.get('target'), selected, data.get('policy'), expected_fingerprint=reviewed['fingerprint'], priority_source_id=priority_source)
                log = logs[0]
                messages.success(request, f'Подразделения объединены под именем «{log.target}». Повторите расчёт планов продаж. Для расчёта MS SQL синхронизируйте настройки кнопкой ниже.')
                return redirect('motivation_subdivision_merge')
            if data.get('policy') not in ('source', 'target'):
                raise ValidationError('Выберите приоритет коэффициентов перед проверкой.')
            if data.get('policy') == 'source' and priority_source not in source_ids:
                raise ValidationError('Выберите присоединяемое подразделение с приоритетом коэффициентов.')
            preview_token = signing.dumps({'selection': selection(), 'fingerprint': info['fingerprint']}, salt='subdivision-merge-review')
        except (ValidationError, Subdivision.DoesNotExist, ValueError, TypeError) as error:
            errors = error.messages if isinstance(error, ValidationError) else ['Проверьте выбранные подразделения и версию.']
        except Exception:
            logger.exception('Subdivision merge failed')
            errors = ['Не удалось выполнить действие. Проверьте настройки SQL Server и журнал сервера.']
    branches = list(Subdivision.objects.order_by('subdivision_key'))
    pair_source = next((branch for branch in branches if branch.subdivision_key.strip().casefold() == 'термотрейд'), None)
    pair_target = next((branch for branch in branches if branch.subdivision_key.strip().casefold() == 'д-москва'), None)
    return render(request, 'admin_motivation/subdivision_merge.html', {
        'branches': branches, 'pair_source': pair_source, 'pair_target': pair_target, 'versions': GlobalCoeffVersion.objects.exclude(status='superseded').order_by('-pk'),
        'selected_version': str(selected), 'values': data, 'selected_sources': source_ids, 'priority_source': priority_source, 'preview': info if preview_token else None, 'preview_token': preview_token, 'errors': errors,
        'logs': SubdivisionMergeLog.objects.select_related('source', 'target', 'version').order_by('-pk')[:20]})


@require_http_methods(['POST'])
def reopen_coefficient_version(request):
    admin_required(request)
    from .services.version_reopen import reopen_version
    try:
        if request.POST.get('confirm') != 'yes':
            raise ValidationError('Подтвердите возврат версии в черновик.')
        version = reopen_version(request.user, request.POST.get('version'), request.POST.get('revision'), request.POST.get('reason', ''))
        messages.success(request, 'Версия возвращена в черновик. Прежние значения сохранены в журнале возвратов. Уже выполненные расчёты и MS SQL автоматически не изменены: после исправлений утвердите версию, повторите выгрузку и расчёт.')
        return redirect(reverse('global_coeff_admin_motivation') + f'?version={version.pk}')
    except (ValidationError, ValueError, TypeError) as error:
        messages.error(request, ' '.join(error.messages) if isinstance(error, ValidationError) else 'Проверьте выбранную версию.')
        return redirect(reverse('global_coeff_admin_motivation'))


@require_http_methods(['POST'])
def bulk_coefficient_formula(request):
    from base.access import motivation_editor_required
    motivation_editor_required(request)
    from .services.bulk_formula import apply_formula, preview_formula
    from django.core import signing
    from hashlib import sha256
    import json
    data=request.POST
    review_data={key:sorted(data.getlist(key)) for key in ('version','revision','goods','variation','kinds','subdivision','employee')}
    digest=sha256(json.dumps(review_data,sort_keys=True).encode()).hexdigest()
    args=(data.get('version'),data.get('revision'),data.getlist('goods'),data.get('variation'),data.getlist('kinds'))
    kwargs={'subdivision_id':data.get('subdivision') or None,'employee_id':data.get('employee') or None}
    subdivision=data.get('subdivision')
    route=reverse('coeff_subdivisions_admin_motivation',args=[subdivision]) if subdivision and subdivision.isdigit() else reverse('global_coeff_admin_motivation')
    try:
        if data.get('preview')=='1':
            info=preview_formula(*args,**kwargs)
            info['token']=signing.dumps({'digest':digest,'actor':request.user.pk},salt='bulk-formula-review')
            return JsonResponse(info)
        if data.get('review_required')=='yes':
            signed=signing.loads(data.get('review_token',''),salt='bulk-formula-review',max_age=900)
            if signed != {'digest':digest,'actor':request.user.pk}:
                raise ValidationError('Выбор изменился после проверки. Проверьте операцию повторно.')
        goods, count=apply_formula(*args,actor=request.user,**kwargs)
        messages.success(request,f'Формула применена к товарам: {goods}, записей коэффициентов: {count}. Проценты коэффициентов сохранены.')
    except (ValidationError,ValueError,TypeError,signing.BadSignature) as error:
        text=' '.join(error.messages) if isinstance(error,ValidationError) else 'Проверьте версию, формулу и товары. Повторите предварительную проверку.'
        if data.get('preview')=='1': return JsonResponse({'error':text},status=400)
        messages.error(request,text)
    version=data.get('version','')
    suffix=f'?version={version}' if version.isdigit() else ''
    employee=data.get('employee','')
    if subdivision and employee.isdigit(): suffix+=('&' if suffix else '?')+f'employee={employee}'
    return redirect(route+suffix)


@require_http_methods(['GET'])
def version_reopen_snapshot(request, log_id):
    admin_required(request)
    import json
    from django.http import HttpResponse
    from .models import VersionReopenLog
    log = get_object_or_404(VersionReopenLog, pk=log_id)
    response = HttpResponse(json.dumps(log.snapshot, ensure_ascii=False, indent=2), content_type='application/json; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="version-{log.version_id}-before-reopen-{log.pk}.json"'
    return response


@require_http_methods(['GET'])
def coefficient_edit_log(request, log_id):
    from base.access import motivation_editor_required
    from .models import CoefficientEditLog
    from .services.edit_history import rows
    from django.shortcuts import get_object_or_404
    from django.http import HttpResponse
    import csv
    motivation_editor_required(request)
    log = get_object_or_404(CoefficientEditLog, pk=log_id)
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="coefficient-changes-{log.pk}.csv"'
    response.write('\ufeff')
    writer = csv.writer(response, delimiter=';')
    writer.writerow(['Дата, МСК', 'Автор', 'Область', 'Версия', 'Товар / настройка', 'Поле', 'Было', 'Стало'])
    from zoneinfo import ZoneInfo
    prefix = [log.created_at.astimezone(ZoneInfo('Europe/Moscow')).strftime('%d.%m.%Y %H:%M:%S'), log.actor_label, log.scope_label, str(log.version)]
    for row in rows(log):
        writer.writerow(["'"+value if value.lstrip().startswith(('=', '+', '-', '@')) else value for value in [*prefix,*row]])
    return response
