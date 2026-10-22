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
    run_at = forms.DateTimeField(label='Дата и время (Москва)', required=False,
                                 widget=forms.DateTimeInput(attrs={'type': 'datetime-local'}, format='%Y-%m-%dT%H:%M'))
    class Meta:
        model = MotivationCalculationSchedule
        fields = ['name', 'mode', 'run_at', 'enabled']

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
                job = form.save()
                calculation_jobs.sync_job(job)
                job.synced, job.error = True, ''
                job.save(update_fields=['synced', 'error'])
                messages.success(request, 'Задание сохранено в SQL Server Agent.')
                return redirect('motivation_calculations')
        except Exception as error:
            text = ' '.join(error.messages) if isinstance(error, ValidationError) else 'Не удалось настроить или запустить SQL Agent. Проверьте DWH_SQL_CONN_STR, службу Agent и права на создание заданий и процедур; подробности в журнале.'
            logger.exception('Motivation calculation job operation failed')
            if job:
                job.synced, job.error = False, text
                job.save(update_fields=['synced', 'error'])
            messages.error(request, text)
    jobs = list(MotivationCalculationSchedule.objects.order_by('pk'))
    for job in jobs:
        job.current_status = 'Не синхронизировано'
        if job.synced:
            try:
                job.current_status = calculation_jobs.status(job)
            except Exception:
                job.current_status = 'Статус SQL Server недоступен'
    return render(request, 'admin_motivation/calculations.html', {'form': form, 'jobs': jobs})


@require_http_methods(['GET', 'POST'])
def merge_subdivisions(request):
    admin_required(request)
    data = request.POST if request.method == 'POST' else request.GET
    info, errors, preview_token = None, [], None
    selected = data.get('version') or GlobalCoeffVersion.objects.exclude(status='superseded').order_by('-pk').values_list('pk', flat=True).first()
    if request.method == 'POST':
        try:
            if data.get('action') == 'sync_sql':
                calculation_jobs.sync_merge_configuration()
                messages.success(request, 'Объединение и утверждённые коэффициенты синхронизированы с SQL Server. Запускайте расчёт из административного раздела портала.')
                return redirect('motivation_subdivision_merge')
            info = subdivision_merge.preview(data.get('source'), data.get('target'), selected)
            if data.get('action') == 'apply':
                if data.get('confirm') != 'yes':
                    raise ValidationError('Подтвердите объединение после проверки предварительного результата.')
                try:
                    reviewed = signing.loads(data.get('preview_token', ''), salt='subdivision-merge-review', max_age=1800)
                except signing.BadSignature:
                    raise ValidationError('Сначала проверьте объединение. Предварительный просмотр действует 30 минут.')
                if reviewed['selection'] != [str(data.get('source')), str(data.get('target')), str(selected), data.get('policy')]:
                    raise ValidationError('Параметры изменились после проверки. Нажмите «Проверить объединение» ещё раз.')
                log = subdivision_merge.apply(data.get('source'), data.get('target'), selected, data.get('policy'), expected_fingerprint=reviewed['fingerprint'])
                messages.success(request, f'Подразделения объединены под именем «{log.target}». Повторите расчёт планов продаж. Для расчёта MS SQL синхронизируйте настройки кнопкой ниже.')
                return redirect('motivation_subdivision_merge')
            if data.get('policy') not in ('source', 'target'):
                raise ValidationError('Выберите приоритет коэффициентов перед проверкой.')
            preview_token = signing.dumps({'selection': [str(data.get('source')), str(data.get('target')), str(selected), data.get('policy')], 'fingerprint': info['fingerprint']}, salt='subdivision-merge-review')
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
        'selected_version': str(selected), 'values': data, 'preview': info if preview_token else None, 'preview_token': preview_token, 'errors': errors,
        'logs': SubdivisionMergeLog.objects.select_related('source', 'target', 'version').order_by('-pk')[:20]})
