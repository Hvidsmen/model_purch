"""Private fact-2025 / plan-2025 report and explicit manual test launch."""
import logging
from django import forms
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.http import FileResponse, Http404
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.views.decorators.http import require_http_methods
from base.access import role_for, can_access, ADMIN, DIRECTOR, FINANCIER, MODELER, MOTIVATION_VIEWER
from .models import MotivationCalculationSchedule, Motivation2025Input
from .services.recalculation_2025 import VARIANT, RULE_VERSION, PERIODS, read_coefficients, summary, workbook
from .services import calculation_jobs, execution_tracking
from .conns import connect_database


def can_view(user):
    return bool(user.is_authenticated and user.is_active and role_for(user, 'motivation') in
                (ADMIN, DIRECTOR, FINANCIER, MODELER, MOTIVATION_VIEWER))


class InputForm(forms.Form):
    plan_version = forms.ChoiceField(label='План продаж за 2025 год')
    coefficients = forms.FileField(label='Коэффициенты политик и продаж · XLSX', help_text='Загрузите согласованный соeffs.xlsx. Ставки сохраняются только в базе портала.')
    quarter_share = forms.DecimalField(label='Квартальная доля, %', min_value=0, max_value=100, decimal_places=6, initial=80)
    year_share = forms.DecimalField(label='Годовая доля, %', min_value=0, max_value=100, decimal_places=6, initial=20)
    payout_threshold = forms.DecimalField(label='Порог выплаты (0,8 = 80%)', min_value=0, max_value=1, decimal_places=8, initial='.8')

    def __init__(self, *args, **kwargs):
        self.saved_input = kwargs.pop('saved_input', None)
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.error_messages['required'] = 'Обязательное поле.'
        if self.saved_input is not None:
            self.fields['coefficients'].required = False
            self.fields['coefficients'].help_text = 'Новый файл необязателен: используются сохранённые коэффициенты, если его не выбрать.'
        from .operation_views import loaded_plan_versions
        choices = [('', 'Выберите загруженный план')] + [(value, value) for value in loaded_plan_versions()]
        saved_plan = self.initial.get('plan_version')
        if saved_plan and saved_plan not in dict(choices):
            choices.append((saved_plan, saved_plan + ' · сохранённый план'))
        self.fields['plan_version'].choices = choices

    def clean_coefficients(self):
        file = self.cleaned_data['coefficients']
        if file is None and self.saved_input is not None:
            self.rows, self.sha256 = self.saved_input.coefficients, self.saved_input.source_sha256
            self.source_name = self.saved_input.source_name
        else:
            self.rows, self.sha256 = read_coefficients(file)
            self.source_name = file.name
        return file

    def clean(self):
        data = super().clean()
        if data.get('quarter_share') is not None and data.get('year_share') is not None and data['quarter_share'] + data['year_share'] != 100:
            raise ValidationError('Квартальная и годовая доли должны в сумме составлять 100%.')
        return data


def read_results(job, connector=None, *, include_performance=False):
    """Hold the shared calculation lock while checking run identity AND reading rows."""
    connection = None
    try:
        connection, cursor = (connector or connect_database)('vm-dwh', 'DataWH')
        connection.timeout = 30
        cursor.execute("DECLARE @r int; EXEC @r=sys.sp_getapplock @Resource=N'PortalMotivationCalculation',@LockMode=N'Shared',@LockOwner=N'Session',@LockTimeout=0; SELECT @r;")
        lock = cursor.fetchone()
        if not lock or lock[0] < 0:
            raise ValidationError('Выполняется расчёт. Дождитесь завершения.')
        cursor.execute("SELECT TOP(1) JobKey,State_,JSON_VALUE(Parameters_,'$.recalculation_rule'),Parameters_ FROM motivation.PortalMotivationRun WHERE JSON_VALUE(Parameters_,'$.variant')=N'test_2025' ORDER BY StartedAt DESC")
        row = cursor.fetchone()
        if not row or str(row[0]).casefold() != str(job.key).casefold() or row[1] != 'completed' or row[2] != RULE_VERSION:
            raise ValidationError('Для выбранных входных данных ещё нет завершённого пересчёта 2025 года. Запустите его; старые результаты не подставляются.')
        import json
        actual = json.loads(row[3])
        expected = execution_tracking.parameters(job)
        if any(actual.get(key) != expected.get(key) for key in ('plan_version', 'quarter_share', 'year_share', 'payout_threshold', 'coefficient_sha256')):
            raise ValidationError('Параметры входных данных изменились. Выполните новый пересчёт 2025 года.')
        cursor.execute("""SELECT LTRIM(RTRIM([ТипКоэф])),[Подразделение],[АвторСделки],[КварталМотивации],
 SUM(COALESCE([СуммаМотивацииUSDУчетПроцента],0)),SUM(COALESCE([Остаток],0))
 FROM motivation.ReportMotivationByDealAuthorTest2025
 WHERE [Год]=2025 AND LTRIM(RTRIM([ТипКоэф])) IN (N'Политики',N'Продажи')
 GROUP BY [ТипКоэф],[Подразделение],[АвторСделки],[КварталМотивации]""")
        sections = summary(cursor.fetchall())
        if include_performance:
            from .services.recalculation_2025_performance import DETAIL_SQL, gates_sql, explain
            cursor.execute(DETAIL_SQL)
            detail = cursor.fetchall()
            cursor.execute(gates_sql())
            performance = explain(detail, cursor.fetchall(), job.payout_threshold)
            for section in sections:
                section['performance'] = [row for row in performance if row['kind'] == section['kind']]
        return sections
    finally:
        if connection is not None:
            connection.close()  # Session lock is released even if SQL raised.


@require_http_methods(['GET', 'POST'])
def report(request):
    if not can_view(request.user):
        raise PermissionDenied
    can_start = can_access(request.user, 'motivation_admin')
    if request.method == 'POST' and not can_start:
        raise PermissionDenied
    action = request.POST.get('action') if request.method == 'POST' else None
    job = MotivationCalculationSchedule.objects.filter(variant=VARIANT, recalculation_2025__isnull=False).order_by('-pk').first()
    selected_job = request.POST.get('job') if action == 'run' else request.GET.get('job')
    if selected_job is not None:
        try:
            selected_job = int(selected_job)
        except (ValueError, TypeError):
            raise Http404('Задание не найдено.')
        job = get_object_or_404(MotivationCalculationSchedule, pk=selected_job, variant=VARIANT, recalculation_2025__isnull=False)
    initial = {key: getattr(job, key) for key in ('plan_version', 'quarter_share', 'year_share', 'payout_threshold')} if job else None
    form = InputForm(request.POST if action == 'upload' else None, request.FILES if action == 'upload' else None,
                     initial=initial, saved_input=job.recalculation_2025 if job else None)
    error, sections, state = job.error if job else '', [], None
    progress=None
    agent_history=[];agent_history_unavailable=False;agent_job_name=''
    if request.method == 'POST':
        if not can_start:
            raise PermissionDenied
        if action == 'upload' and form.is_valid():
            with transaction.atomic():
                job = MotivationCalculationSchedule.objects.create(name='Пересчёт 2025', variant=VARIANT,
                    fact_year=2025, plan_version=form.cleaned_data['plan_version'], mode='manual', enabled=False,
                    quarter_share=form.cleaned_data['quarter_share'], year_share=form.cleaned_data['year_share'],
                    payout_threshold=form.cleaned_data['payout_threshold'])
                Motivation2025Input.objects.create(job=job, coefficients=form.rows,
                    source_name=form.source_name[:255], source_sha256=form.sha256)
            messages.success(request, 'Коэффициенты сохранены отдельно для теста. Действующие версии не изменены.')
            return redirect(reverse('motivation_report_2025') + f'?job={job.pk}')
        elif action == 'run':
            try:
                state = calculation_jobs.monitor(job)
                if state.get('status') in ('Выполняется', 'Ожидает запуска'):
                    messages.info(request, 'Это задание уже выполняется. Дождитесь завершения.')
                    return redirect(reverse('motivation_report_2025') + f'?job={job.pk}')
                calculation_jobs.start_job(job)
                job.synced = True; job.error = ''; job.save(update_fields=['synced', 'error'])
                messages.success(request, 'Тестовый пересчёт 2025 запущен. Обновите отчёт после завершения.')
                return redirect(reverse('motivation_report_2025') + f'?job={job.pk}')
            except ValidationError as exc:
                error = ' '.join(exc.messages)
            except Exception as exc:
                from uuid import uuid4
                reference = uuid4().hex[:12]
                logging.getLogger(__name__).exception('2025 test launch failed [%s]', reference)
                error = calculation_jobs.failure_message(exc, reference)
            job.error = error
            job.save(update_fields=['error'])
            return redirect(reverse('motivation_report_2025') + f'?job={job.pk}')
    if job and job.error:
        state = 'start_failed'
    elif job and job.synced:
        observed = execution_tracking.observe(job, persist=can_start)
        state = execution_tracking.running_state(job, observed)
        if state!='queued':
            from .services.recalculation_2025_progress import display
            progress=display(job,observed,state)
        if state=='failed':
            error='Пересчёт завершился с ошибкой. '+('Подробности — в журнале SQL Agent ниже.' if can_start else 'Обратитесь к администратору для проверки журнала SQL Agent.')
            if can_start:
                agent_job_name=calculation_jobs.job_name(job)
                try:agent_history=calculation_jobs.monitor(job).get('history',[])[:5]
                except Exception:
                    agent_history_unavailable=True
                    logging.getLogger(__name__).exception('2025 calculation failure history unavailable')
        if state not in ('failed','interrupted') and (request.GET.get('run') == '1' or request.GET.get('export') == 'xlsx'):
            try:
                sections = read_results(job, include_performance=request.GET.get('export') == 'xlsx')
                if request.GET.get('export') == 'xlsx':
                    return FileResponse(workbook(sections), as_attachment=True, filename='motivation-2025-usd.xlsx')
            except ValidationError as exc:
                error = ' '.join(exc.messages)
            except Exception:
                logging.getLogger(__name__).exception('2025 test report failed')
                error = 'Не удалось прочитать результат пересчёта 2025. Проверьте подключение DataWH.'
    elif job:
        state = 'not_started'
    tab = request.GET.get('tab', 'policies')
    selected = 'Продажи' if tab == 'sales' else 'Политики'
    section = next((s for s in sections if s['kind'] == selected), None)
    return render(request, 'admin_motivation/reports_next/year_2025.html', {
        'form': form, 'can_start': can_start, 'job': job, 'section': section,
        'tab': tab, 'period_labels': PERIODS, 'error': error, 'state': state, 'progress':progress,
        'agent_history':agent_history,'agent_history_unavailable':agent_history_unavailable,'agent_job_name':agent_job_name,
        'input': job.recalculation_2025 if job else None})
