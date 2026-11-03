from datetime import datetime
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo
from django.test import TestCase
from django.urls import reverse
from django.core.exceptions import ValidationError
from base.testing import authorize_test_case
from .models import MotivationCalculationSchedule, SalesPlanScenario
from django.utils import timezone
from .operation_views import JobForm
from .services import calculation_jobs as jobs


class CalculationJobsTests(TestCase):
    def setUp(self):
        authorize_test_case(self)
        for version in ('Test plan', 'Selected plan', 'Other plan'):
            SalesPlanScenario.objects.create(title=version, source_version=version, loaded_at=timezone.now())
        self.job = MotivationCalculationSchedule.objects.create(name='Расчёт', fact_year=2026, plan_version='Test plan')
        self.connection, self.cursor = MagicMock(), MagicMock()
        self.connector = MagicMock(return_value=(self.connection, self.cursor))
        self.cursor.fetchall.return_value = [(name,) for name in ('@FactYear', '@PlanVersion', '@QuarterShare', '@YearShare', '@PayoutThreshold')]

    def test_admin_page_and_manual_run(self):
        url = reverse('motivation_calculations')
        self.assertContains(self.client.get(url), 'Центр расчётов')
        with patch.object(jobs, 'start_job') as start:
            response = self.client.post(url, {'job': self.job.pk, 'action': 'run'})
        self.assertEqual(response.status_code, 302)
        start.assert_called_once()
        self.job.refresh_from_db()
        self.assertTrue(self.job.synced)

    def test_anonymous_cannot_run(self):
        self.client.logout()
        with patch.object(jobs, 'start_job') as start:
            response = self.client.post(reverse('motivation_calculations'), {'job': self.job.pk, 'action': 'run'})
        self.assertIn(response.status_code, (302, 403))
        start.assert_not_called()

    def test_moscow_input_and_future_validation(self):
        form = JobForm({'name': 'Test', 'fact_year': 2026, 'plan_version': 'Test plan', 'quarter_share': 80, 'year_share': 20, 'payout_threshold': '0.8', 'mode': 'daily', 'enabled': 'on', 'run_at': '2030-01-01T12:30'})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['run_at'].utcoffset().total_seconds(), 10800)
        form = JobForm({'name': 'Test', 'fact_year': 2026, 'plan_version': 'Test plan', 'quarter_share': 80, 'year_share': 20, 'payout_threshold': '0.8', 'mode': 'once', 'enabled': 'on', 'run_at': '2000-01-01T12:30'})
        self.assertFalse(form.is_valid())

    @patch.object(jobs, 'prepare')
    def test_daily_schedule_converts_moscow_to_server_time(self, prepare):
        self.job.enabled, self.job.mode = True, 'daily'
        self.job.run_at = datetime(2030, 1, 1, 12, 30, tzinfo=ZoneInfo('Europe/Moscow'))
        self.cursor.fetchone.side_effect = [(1,), None, None, (0,)]
        jobs.sync_job(self.job, self.connector)
        schedule = next(call.args for call in self.cursor.execute.call_args_list if 'sp_add_jobschedule' in call.args[0])
        self.assertEqual(schedule[-3:], (4, 20300101, 93000))
        self.connection.commit.assert_called_once()
        self.connection.close.assert_called_once()

    @patch.object(jobs, 'prepare', side_effect=ValidationError('Unavailable'))
    def test_sync_failure_rolls_back_and_closes(self, prepare):
        with self.assertRaises(ValidationError):
            jobs.sync_job(self.job, self.connector)
        self.connection.rollback.assert_called_once()
        self.connection.commit.assert_not_called()
        self.connection.close.assert_called_once()

    @patch.object(jobs, 'rules', return_value=[('Old', 'New', 'target')])
    def test_prepare_copies_procedure_and_redirects_reference(self, rules):
        definition = 'ALTER PROCEDURE [motivation].[sp_CalcBySub] AS SELECT IIF(SUM(Fact) OVER(PARTITION BY SubdivisionName)/SUM(Plan_) OVER(PARTITION BY SubdivisionName)<0.8,1,0) FROM [DataWH].[dbo].[Subdivisions];'
        from .test_pilot_calculation import procedure
        definition+='\n'+procedure().split(' AS\n',1)[1]
        from .test_director_participation import SOURCE
        definition += '\n' + SOURCE
        self.cursor.fetchone.return_value = (definition, None)
        self.cursor.fetchall.return_value = [('SubdivisionKey',), ('SubdivisionName',)]
        jobs.prepare(self.cursor)
        sql = self.cursor.execute.call_args.args[0]
        self.assertIn('CREATE PROCEDURE motivation.PortalCalcBySub', sql)
        self.assertIn('DataWH.motivation.PortalCalculationSubdivisions', sql)
        self.assertNotIn('ALTER PROCEDURE [motivation].[sp_CalcBySub]', sql)
        self.assertEqual(sql.count("COALESCE(LTRIM(RTRIM([ТипКоэф])),N'')<>N'Директора'"), 2)
        view_sql = next(call.args[0] for call in self.cursor.execute.call_args_list if call.args[0].startswith('ALTER VIEW motivation.PortalCalculationSubdivisions'))
        self.assertIn("N'Вентиляция'",view_sql)
        self.assertIn('COALESCE(m.TargetName,s.SubdivisionName)',view_sql)
        self.assertIn("COALESCE(m.TargetName,s.SubdivisionName)=N'Д-Москва'", view_sql)
        self.cursor.executemany.assert_called_once()

    @patch.object(jobs, 'rules', return_value=[('Old', 'New', 'target')])
    def test_prepare_rejects_unrecognized_merge_source(self, rules):
        self.cursor.fetchone.return_value = ('CREATE PROC motivation.sp_CalcBySub AS SELECT 1;', None)
        self.cursor.fetchall.return_value = [('SubdivisionName',)]
        with self.assertRaises(ValidationError):
            jobs.prepare(self.cursor)

    @patch.object(jobs, 'rules', return_value=[])
    def test_both_variants_scope_plan_fact_and_sales_to_moscow(self, rules):
        from pathlib import Path
        source = (Path(__file__).parent / 'sql/review/sp_CalcBySub.refactored.sql').read_text()
        source = source[source.index('CREATE OR ALTER PROCEDURE'):].replace('@Year', '@FactYear')
        for variant in ('main', 'all_2026'):
            with self.subTest(variant=variant):
                self.cursor.reset_mock()
                self.cursor.fetchone.return_value = (source, None)
                self.cursor.fetchall.return_value = [('SubdivisionKey',), ('SubdivisionName',)]
                jobs.prepare(self.cursor, variant)
                view = next(call.args[0] for call in self.cursor.execute.call_args_list
                            if call.args[0].startswith('ALTER VIEW motivation.PortalCalculationSubdivisions'))
                self.assertIn("COALESCE(m.TargetName,s.SubdivisionName)=N'Д-Москва'", view)
                procedure = self.cursor.execute.call_args.args[0]
                self.assertIn('AND Subdivision IN (SELECT SubdivisionKey FROM DataWH.motivation.PortalCalculationSubdivisions)', procedure)
                self.assertIn('motivation.PortalCalcBySub' if variant == 'main' else 'motivation.PortalCalcAll2026', procedure)
                self.assertNotIn('JOIN DataWH.dbo.Subdivisions', procedure)
                self.assertIn('JOIN DataWH.motivation.PortalCalculationSubdivisions', procedure)

    def test_running_status_comes_from_agent(self):
        self.cursor.fetchone.return_value = (datetime(2030, 1, 1), None, 1, 'Previous completed')
        self.assertEqual(jobs.status(self.job, self.connector), 'Выполняется')
        self.connection.close.assert_called_once()

    def test_job_command_embeds_validated_parameters_and_quotes_plan_name(self):
        self.job.plan_version = "Plan O'Brien; -- test"
        command = jobs.calculation_command(self.job)
        self.assertIn("@PlanVersion=N'Plan O''Brien; -- test'", command)
        self.assertIn('@FactYear=2026', command)
        self.assertIn('@QuarterShare=0.80000000', command)
        self.assertIn('@YearShare=0.20000000', command)
        self.assertIn('sp_getapplock', command)
        self.job.fact_year = None
        with self.assertRaises(ValidationError):
            jobs.calculation_command(self.job)

    def test_form_validates_year_plan_and_share_sum(self):
        data = {'name': 'Job', 'mode': 'manual', 'fact_year': 2025, 'plan_version': 'Selected plan', 'quarter_share': 75, 'year_share': 25, 'payout_threshold': '0.8'}
        self.assertTrue(JobForm(data).is_valid())
        for changes in ({'fact_year': 0}, {'plan_version': ''}, {'year_share': 20, 'payout_threshold': '0.8'}, {'quarter_share': -1}, {'quarter_share': 'NaN'}):
            with self.subTest(changes=changes):
                self.assertFalse(JobForm({**data, **changes}).is_valid())

    @patch.object(jobs, 'prepare')
    def test_stored_agent_step_has_explicit_parameters_and_missing_support_rolls_back(self, prepare):
        self.cursor.fetchone.side_effect = [(1,), None, None]
        jobs.sync_job(self.job, self.connector)
        step = next(call.args for call in self.cursor.execute.call_args_list if 'sp_add_jobstep' in call.args[0])
        self.assertIn('@FactYear=2026', step[-1])
        self.assertIn("@PlanVersion=N'Test plan'", step[-1])
        self.cursor.reset_mock()
        self.connection.reset_mock()
        self.cursor.fetchall.return_value = []
        with self.assertRaisesMessage(ValidationError, 'не поддерживает параметры'):
            jobs.sync_job(self.job, self.connector)
        self.connection.rollback.assert_called_once()
        self.assertFalse(any('sp_update_jobstep' in call.args[0] for call in self.cursor.execute.call_args_list))

    @patch.object(jobs, 'sync_job')
    def test_editing_job_persists_parameters_only_after_successful_sql_sync(self, sync):
        data = {'job': self.job.pk, 'name': 'Updated', 'mode': 'manual', 'fact_year': 2025,
                'plan_version': 'Other plan', 'quarter_share': 70, 'year_share': 30, 'payout_threshold': '0.8'}
        response = self.client.post(reverse('motivation_calculations'), data)
        self.assertEqual(response.status_code, 302)
        self.job.refresh_from_db()
        self.assertEqual(self.job.fact_year, 2025)
        self.assertEqual(str(self.job.quarter_share), '70.000000')
        sync.side_effect = ValidationError('Unsupported procedure')
        self.client.post(reverse('motivation_calculations'), {**data, 'fact_year': 2023})
        self.job.refresh_from_db()
        self.assertEqual(self.job.fact_year, 2025)
        self.assertFalse(self.job.synced)

    def test_job_plan_choices_only_include_loaded_active_portal_versions(self):
        SalesPlanScenario.objects.create(title='Not imported', source_version='Remote only')
        SalesPlanScenario.objects.create(title='Deleted', source_version='Deleted plan', loaded_at=timezone.now(), is_deleted=True)
        SalesPlanScenario.objects.create(title='Second name', source_version='Test plan', loaded_at=timezone.now())
        field = JobForm().fields['plan_version']
        values = [value for value, label in field.choices]
        self.assertEqual(values.count('Test plan'), 1)
        self.assertNotIn('Remote only', values)
        self.assertNotIn('Deleted plan', values)
        data = {'name': 'Job', 'mode': 'manual', 'fact_year': 2026, 'plan_version': 'Remote only', 'quarter_share': 80, 'year_share': 20, 'payout_threshold': '0.8'}
        self.assertIn('plan_version', JobForm(data).errors)
        data['plan_version'] = 'Test plan'
        form = JobForm(data)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save(commit=False).plan_version, 'Test plan')

    def test_monitor_reads_only_own_agent_job_history_and_converts_moscow_time(self):
        self.cursor.fetchone.return_value = (datetime(2026, 10, 23, 23, 30), None, 1, 'Previous success', datetime(2026, 10, 23, 23, 29), 0)
        self.cursor.fetchall.return_value = [
            (91, 20261023, 233000, 253045, 0, '<script>SQL error</script>'),
            (90, 20261022, 10000, 42, 1, 'Completed')]
        result = jobs.monitor(self.job, self.connector)
        self.assertEqual(result['status'], 'Выполняется')
        self.assertEqual(result['history'][0]['started'], '24.10.2026 02:30:00')
        self.assertEqual(result['history'][0]['duration'], '25:30:45')
        self.assertEqual(result['history'][0]['status'], 'Ошибка')
        self.assertEqual(result['history'][1]['duration'], '00:00:42')
        self.assertEqual(self.cursor.execute.call_count, 2)
        for call in self.cursor.execute.call_args_list:
            self.assertEqual(call.args[1], jobs.job_name(self.job))
            self.assertTrue(call.args[0].lstrip().startswith('SELECT'))
        self.connection.close.assert_called_once()
        self.connection.commit.assert_not_called()

    def test_monitor_unknown_job_and_failed_connection_close_safely(self):
        self.cursor.fetchone.return_value = None
        self.assertEqual(jobs.monitor(self.job, self.connector)['history'], [])
        self.cursor.execute.side_effect = RuntimeError('Private connection details')
        with self.assertRaises(RuntimeError):
            jobs.monitor(self.job, self.connector)
        self.assertEqual(self.connection.close.call_count, 2)

    def test_monitor_endpoint_is_admin_only_read_only_and_not_cached(self):
        url = reverse('motivation_calculation_monitor', args=[self.job.pk])
        with patch.object(jobs, 'monitor', return_value={'status': 'Завершено', 'history': []}) as monitor:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()['status'], 'Завершено')
            self.assertIn('checked_at', response.json())
            self.assertIn('no-store', response['Cache-Control'])
            self.assertEqual(self.client.post(url).status_code, 405)
            monitor.assert_called_once()
            self.client.logout()
            self.assertIn(self.client.get(url).status_code, (302, 401, 403))
            monitor.assert_called_once()
        from django.contrib.auth.models import User, Group
        from base.access import BRANCH_DIRECTOR
        user = User.objects.create_user('monitor-denied')
        user.groups.add(Group.objects.get(name=BRANCH_DIRECTOR))
        self.client.force_login(user)
        with patch.object(jobs, 'monitor') as monitor:
            self.assertEqual(self.client.get(url).status_code, 403)
            monitor.assert_not_called()

    def test_monitor_failure_returns_safe_error_without_claiming_completion(self):
        with patch.object(jobs, 'monitor', side_effect=RuntimeError('Private connection details')):
            response = self.client.get(reverse('motivation_calculation_monitor', args=[self.job.pk]))
        self.assertEqual(response.status_code, 503)
        self.assertNotContains(response, 'Private connection details', status_code=503)
        self.assertNotIn('status', response.json())

    def test_monitor_distinguishes_queued_run_from_previous_success(self):
        self.cursor.fetchone.return_value = (None, None, 1, 'Previous success', datetime(2026, 10, 24, 12), 180)
        self.cursor.fetchall.return_value = []
        self.assertEqual(jobs.monitor(self.job, self.connector)['status'], 'Ожидает запуска')


class CalculationFailureDiagnosticsTests(TestCase):
    def test_prepare_failure_has_stage_and_rolls_back(self):
        job = MotivationCalculationSchedule(fact_year=2026, plan_version='Test')
        connection, cursor = MagicMock(), MagicMock()
        with patch.object(jobs, 'prepare', side_effect=RuntimeError('private details')):
            with self.assertRaises(jobs.JobOperationError) as caught:
                jobs.sync_job(job, lambda *args: (connection, cursor))
        self.assertEqual(caught.exception.stage, 'Подготовка расчётной процедуры')
        connection.rollback.assert_called_once()
        connection.commit.assert_not_called()
        connection.close.assert_called_once()
        text = jobs.failure_message(caught.exception, 'test123')
        self.assertIn('Подготовка расчётной процедуры', text)
        self.assertNotIn('private details', text)
        self.assertIn('test123', text)

    def test_sql_diagnostic_does_not_expose_connection_or_query(self):
        import pyodbc
        error = jobs.JobOperationError('Подготовка расчётной процедуры', pyodbc.ProgrammingError(
            '42000', "[SQL Server]Incorrect syntax near 'PWD=private-secret;SELECT private_data' (102) (SQLExecDirectW)"))
        text = jobs.failure_message(error, 'ref123')
        self.assertIn('SQLSTATE 42000', text)
        self.assertIn('SQL 102', text)
        self.assertIn('Ошибка синтаксиса SQL', text)
        self.assertNotIn('private', text)

    def test_missing_connection_and_validation_are_actionable(self):
        text = jobs.failure_message(RuntimeError('Не настроено подключение к SQL Server: задайте DWH_SQL_CONN_STR'), 'ref')
        self.assertIn('перезапустите Django', text)
        self.assertEqual(jobs.failure_message(ValidationError('Проверьте план'), 'ref'), 'Проверьте план')

    def test_admin_run_failure_is_persisted_with_reference(self):
        authorize_test_case(self)
        job = MotivationCalculationSchedule.objects.create(name='Test', fact_year=2026, plan_version='Test')
        with patch.object(jobs, 'start_job', side_effect=jobs.JobOperationError('Запуск задания SQL Agent', RuntimeError('secret'))):
            response = self.client.post(reverse('motivation_calculations'), {'job': job.pk, 'action': 'run'})
        self.assertContains(response, 'Запуск задания SQL Agent')
        self.assertContains(response, 'Код обращения:')
        self.assertNotContains(response, 'secret')
        job.refresh_from_db()
        self.assertFalse(job.synced)
        self.assertIn('Код обращения:', job.error)


class CalculationPagePerformanceTests(TestCase):
    def setUp(self):
        authorize_test_case(self)
        SalesPlanScenario.objects.create(title='Plan', source_version='Plan', loaded_at=timezone.now())

    def test_page_does_not_scan_coefficients_or_compute_workflow(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        for index in range(8):
            MotivationCalculationSchedule.objects.create(name=f'Job {index}', fact_year=2026, plan_version='Plan')
        with patch('admin_motivation.services.coefficient_coverage.plan_warning', side_effect=AssertionError('Expensive coverage scan')), patch('admin_motivation.services.workflow.context', side_effect=AssertionError('Expensive workflow snapshot')), CaptureQueriesContext(connection) as queries:
            response = self.client.get(reverse('motivation_calculations'))
        self.assertContains(response, 'Job 7')
        self.assertContains(response, 'Проверить покрытие плана')
        plan_queries = [query for query in queries if 'admin_motivation_salesplanscenario' in query['sql'].lower()]
        self.assertLessEqual(len(plan_queries), 2)

    def test_light_monitor_does_not_rebuild_workflow(self):
        job = MotivationCalculationSchedule.objects.create(name='Fast', fact_year=2026, plan_version='Plan')
        with patch.object(jobs, 'monitor', return_value={'status': 'Выполняется', 'history': []}), patch('admin_motivation.services.execution_tracking.observe', return_value=None), patch('admin_motivation.services.workflow.context', side_effect=AssertionError('Expensive workflow snapshot')):
            response = self.client.get(reverse('motivation_calculation_monitor', args=[job.pk]), {'light':'1'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'Выполняется')
        self.assertNotIn('workflow', response.json())

    def test_unchanged_execution_poll_does_not_write_sqlite(self):
        import json
        from uuid import uuid4
        from .models import MotivationExecution
        from .services.execution_tracking import observe
        job = MotivationCalculationSchedule.objects.create(name='Poll', fact_year=2026, plan_version='Plan')
        now = timezone.now()
        execution = MotivationExecution.objects.create(run_id=uuid4(), job_key=job.key, state='completed', started_at=now, completed_at=now, parameters={'fact_year':2026}, export_snapshot={})
        connection, cursor = MagicMock(), MagicMock()
        cursor.fetchone.return_value = (execution.run_id, str(job.key).upper(), now.replace(tzinfo=None), now.replace(tzinfo=None), 'completed', json.dumps(execution.parameters), '{}')
        with patch.object(MotivationExecution.objects, 'update_or_create', side_effect=AssertionError('Unnecessary write')):
            actual = observe(job, connector=lambda *args: (connection, cursor))
        self.assertEqual(actual.pk, execution.pk)
