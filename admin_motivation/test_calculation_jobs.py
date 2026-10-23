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
        self.cursor.fetchall.return_value = [(name,) for name in ('@FactYear', '@PlanVersion', '@QuarterShare', '@YearShare')]

    def test_admin_page_and_manual_run(self):
        url = reverse('motivation_calculations')
        self.assertContains(self.client.get(url), 'Расчёт и задания')
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
        form = JobForm({'name': 'Test', 'fact_year': 2026, 'plan_version': 'Test plan', 'quarter_share': 80, 'year_share': 20, 'mode': 'daily', 'enabled': 'on', 'run_at': '2030-01-01T12:30'})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['run_at'].utcoffset().total_seconds(), 10800)
        form = JobForm({'name': 'Test', 'fact_year': 2026, 'plan_version': 'Test plan', 'quarter_share': 80, 'year_share': 20, 'mode': 'once', 'enabled': 'on', 'run_at': '2000-01-01T12:30'})
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
        definition = 'ALTER PROCEDURE [motivation].[sp_CalcBySub] AS SELECT SubdivisionName FROM [DataWH].[dbo].[Subdivisions];'
        self.cursor.fetchone.return_value = (definition, None)
        self.cursor.fetchall.return_value = [('SubdivisionKey',), ('SubdivisionName',)]
        jobs.prepare(self.cursor)
        sql = self.cursor.execute.call_args.args[0]
        self.assertIn('CREATE PROCEDURE motivation.PortalCalcBySub', sql)
        self.assertIn('DataWH.motivation.PortalCalculationSubdivisions', sql)
        self.assertNotIn('ALTER PROCEDURE [motivation].[sp_CalcBySub]', sql)
        self.cursor.executemany.assert_called_once()

    @patch.object(jobs, 'rules', return_value=[('Old', 'New', 'target')])
    def test_prepare_rejects_unrecognized_merge_source(self, rules):
        self.cursor.fetchone.return_value = ('CREATE PROC motivation.sp_CalcBySub AS SELECT 1;', None)
        self.cursor.fetchall.return_value = [('SubdivisionName',)]
        with self.assertRaises(ValidationError):
            jobs.prepare(self.cursor)

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
        data = {'name': 'Job', 'mode': 'manual', 'fact_year': 2025, 'plan_version': 'Selected plan', 'quarter_share': 75, 'year_share': 25}
        self.assertTrue(JobForm(data).is_valid())
        for changes in ({'fact_year': 0}, {'plan_version': ''}, {'year_share': 20}, {'quarter_share': -1}, {'quarter_share': 'NaN'}):
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
                'plan_version': 'Other plan', 'quarter_share': 70, 'year_share': 30}
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
        data = {'name': 'Job', 'mode': 'manual', 'fact_year': 2026, 'plan_version': 'Remote only', 'quarter_share': 80, 'year_share': 20}
        self.assertIn('plan_version', JobForm(data).errors)
        data['plan_version'] = 'Test plan'
        form = JobForm(data)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save(commit=False).plan_version, 'Test plan')
