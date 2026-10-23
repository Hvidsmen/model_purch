import logging
from zoneinfo import ZoneInfo
from django import forms
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.core import signing
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods
from base.access import can_access
from .models import Subdivision, GlobalCoeffVersion, MotivationCalculationSchedule, SubdivisionMergeLog
from .services import calculation_jobs, subdivision_merge

logger = logging.getLogger(__name__)


class JobForm(forms.ModelForm):
    fact_year = forms.IntegerField(label='Год факта', min_value=1, max_value=9999)
    plan_version = forms.CharField(label='Версия плана продаж в MS SQL', max_length=500,
                                  widget=forms.TextInput(attrs={'list': 'calculation-plan-versions'}))
    quarter_share = forms.DecimalField(label='Квартальная доля, %', min_value=0, max_value=100, max_digits=9, decimal_places=6, initial=80)
    year_share = forms.DecimalField(label='Годовая доля, %', min_value=0, max_value=100, max_digits=9, decimal_places=6, initial=20)
    run_at = forms.DateTimeField(label='Дата и время (Москва)', required=False,
                                 widget=forms.DateTimeInput(attrs={'type': 'datetime-local'}, format='%Y-%m-%dT%H:%M'))
    class Meta:
        model = MotivationCalculationSchedule
        fields = ['name', 'fact_year', 'plan_version', 'quarter_share', 'year_share', 'mode', 'run_at', 'enabled']

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
    if not can_access(request.user, 'admin'):
        raise PermissionDenied


@require_http_methods(['GET', 'POST'])
def calculations(request):
    admin_required(request)
    form = JobForm()
    if request.method == 'POST':
        job = get_object_or_404(MotivationCalculationSchedule, pk=request.POST['job']) if request.POST.get('job') else None
        try:
            if request.POST.get('action') == 'run' and job:
                calculation_jobs.start_job(job)
                job.synced, job.error = True, ''
                job.save(update_fields=['synced', 'error'])
                messages.success(request, 'Задание отправлено в SQL Server Agent. Завершение проверяется по статусу SQL Server, а не по времени ожидания.')
                return redirect('motivation_calculations')
            form = JobForm(request.POST, instance=job)
            if form.is_valid():
                job = form.save(commit=False)
                calculation_jobs.sync_job(job)
                job.synced, job.error = True, ''
                job.save()
                messages.success(request, 'Задание сохранено в SQL Server Agent.')
                return redirect('motivation_calculations')
        except Exception as error:
            text = ' '.join(error.messages) if isinstance(error, ValidationError) else 'Не удалось настроить или запустить SQL Agent. Проверьте DWH_SQL_CONN_STR, службу Agent и права на создание заданий и процедур; подробности в журнале.'
            logger.exception('Motivation calculation job operation failed')
            if job and job.pk:
                job.synced, job.error = False, text
                job.save(update_fields=['synced', 'error'])
            messages.error(request, text)
    plan_versions = []
    if request.GET.get('load_plans') == '1':
        try:
            from .services.sales_plans import source_versions
            plan_versions = source_versions()
        except Exception:
            messages.error(request, 'Не удалось получить версии плана из MS SQL. Можно ввести точное название вручную.')
    jobs = list(MotivationCalculationSchedule.objects.order_by('pk'))
    for job in jobs:
        job.edit_form = JobForm(instance=job, auto_id=f'job_{job.pk}_%s')
        if job.run_at:
            job.edit_form.initial['run_at'] = job.run_at.astimezone(ZoneInfo('Europe/Moscow')).replace(tzinfo=None)
        job.current_status = 'Не синхронизировано'
        if job.synced:
            try:
                job.current_status = calculation_jobs.status(job)
            except Exception:
                job.current_status = 'Статус SQL Server недоступен'
    return render(request, 'admin_motivation/calculations.html', {'form': form, 'jobs': jobs, 'plan_versions': plan_versions})


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
    admin_required(request)
    from .services.bulk_formula import apply_formula
    from .models import VariationCalculate
    subdivision = request.POST.get('subdivision')
    route = reverse('coeff_subdivisions_admin_motivation', args=[subdivision]) if subdivision and subdivision.isdigit() else reverse('global_coeff_admin_motivation')
    try:
        count, cells = apply_formula(request.POST.get('version'), request.POST.get('revision'), request.POST.getlist('goods'), request.POST.get('variation'), request.POST.getlist('kinds'), subdivision)
        messages.success(request, f'Формула применена к товарам: {count}, ячеек коэффициентов: {cells}. Проценты коэффициентов сохранены.')
    except (ValidationError, VariationCalculate.DoesNotExist, ValueError, TypeError) as error:
        messages.error(request, ' '.join(error.messages) if isinstance(error, ValidationError) else 'Проверьте выбранную формулу и товары.')
    version = request.POST.get('version', '')
    return redirect(route + (f'?version={version}' if version.isdigit() else ''))


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
