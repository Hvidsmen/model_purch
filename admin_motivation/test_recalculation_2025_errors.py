from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import patch
from django.test import TestCase
from . import test_recalculation_2025 as helpers


class FailurePageTests(TestCase):
    setUp=helpers.Recalculation2025WebTests.setUp
    make_job=helpers.Recalculation2025WebTests.make_job

    def page(self,*,history=None,unavailable=False,can_start=True,suffix='?run=1'):
        job=self.make_job();job.synced=True;job.save(update_fields=['synced'])
        with ExitStack() as stack:
            stack.enter_context(patch('admin_motivation.services.execution_tracking.observe',return_value=SimpleNamespace(state='failed')))
            stack.enter_context(patch('admin_motivation.services.execution_tracking.running_state',return_value='failed'))
            stack.enter_context(patch('admin_motivation.services.recalculation_2025_progress.display',return_value=None))
            if not can_start:stack.enter_context(patch('admin_motivation.reports_2025.can_access',return_value=False))
            monitor=stack.enter_context(patch('admin_motivation.services.calculation_jobs.monitor',
                side_effect=RuntimeError('Private credentials') if unavailable else None,
                return_value={'history':history or []}))
            read=stack.enter_context(patch('admin_motivation.reports_2025.read_results'))
            start=stack.enter_context(patch('admin_motivation.services.calculation_jobs.start_job'))
            response=self.client.get(self.url+suffix)
        return job,response,monitor,read,start

    def test_failure_message_and_dated_sql_step_output_replace_generic_missing_result(self):
        job,response,monitor,read,start=self.page(history=[{'started':'11.11.2026 02:40:25',
            'status':'Ошибка','duration':'00:01:00','message':'Failure detail <sample>'}])
        self.assertContains(response,'Пересчёт завершился с ошибкой')
        self.assertContains(response,'Журнал SQL Agent')
        self.assertContains(response,'11.11.2026 02:40:25')
        self.assertContains(response,'Failure detail &lt;sample&gt;')
        self.assertNotContains(response,'Failure detail <sample>')
        self.assertNotContains(response,'ещё нет завершённого пересчёта')
        self.assertContains(response,'PortalMotivation_'+job.key.hex)
        monitor.assert_called_once_with(job);read.assert_not_called();start.assert_not_called()

    def test_log_failure_preserves_error_and_never_exposes_connection_exception(self):
        job,response,monitor,read,start=self.page(unavailable=True)
        self.assertContains(response,'Не удалось прочитать журнал SQL Agent')
        self.assertNotContains(response,'Private credentials')
        self.assertContains(response,'Пересчёт завершился с ошибкой');read.assert_not_called()

    def test_viewer_without_launch_rights_does_not_receive_sql_log(self):
        job,response,monitor,read,start=self.page(can_start=False)
        self.assertContains(response,'Обратитесь к администратору')
        self.assertNotContains(response,'<summary>Журнал SQL Agent')
        monitor.assert_not_called();read.assert_not_called();start.assert_not_called()

    def test_failed_excel_request_returns_failure_details_instead_of_old_export(self):
        job,response,monitor,read,start=self.page(suffix='?export=xlsx')
        self.assertContains(response,'Пересчёт завершился с ошибкой')
        self.assertFalse(response.has_header('Content-Disposition'));read.assert_not_called()
