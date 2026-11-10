from datetime import datetime,timedelta
from types import SimpleNamespace
from uuid import uuid4
from unittest.mock import Mock,patch
from django.test import SimpleTestCase,TestCase
from .services.execution_tracking import running_state
from . import test_recalculation_2025 as helpers


class InterruptedRunStatusTests(SimpleTestCase):
    def setUp(self):
        self.conn=Mock();self.cursor=Mock();self.connector=Mock(return_value=(self.conn,self.cursor))
        self.job=SimpleNamespace(key=uuid4());self.execution=SimpleNamespace(state='running',run_id=uuid4())
        self.started=datetime(2026,11,11,1);self.stopped=self.started+timedelta(minutes=1)

    def state(self,row,journal='running'):
        self.cursor.fetchone.side_effect=[row,(journal,)]
        return running_state(self.job,self.execution,self.connector)

    def test_stopped_agent_and_free_lock_unblocks_interrupted_run(self):
        self.assertEqual(self.state((self.started,self.stopped,self.started,1)),'interrupted')
        self.conn.close.assert_called_once()
        statements=[c.args[0] for c in self.cursor.execute.call_args_list]
        self.assertTrue(all(s.lstrip().startswith('SELECT') for s in statements))
        self.assertIn('APPLOCK_TEST',statements[0]);self.assertNotIn('sp_getapplock',statements[0])

    def test_active_agent_stays_running_even_if_lock_probe_is_free(self):
        self.assertEqual(self.state((self.started,None,self.started,1)),'running')
        self.assertEqual(self.cursor.execute.call_count,1)

    def test_queued_retry_is_not_declared_interrupted(self):
        self.assertEqual(self.state((self.started,self.stopped,self.stopped+timedelta(seconds=1),1)),'queued')

    def test_rollback_or_another_writer_prevents_claiming_stopped(self):
        self.assertEqual(self.state((self.started,self.stopped,self.started,0)),'unknown')

    def test_journal_recheck_handles_completion_race(self):
        self.assertEqual(self.state((self.started,self.stopped,self.started,1),'completed'),'completed')

    def test_journal_recheck_handles_failure(self):
        self.assertEqual(self.state((self.started,self.stopped,self.started,1),'failed'),'failed')

    def test_missing_agent_after_restart_can_be_interrupted_if_lock_is_free(self):
        self.assertEqual(self.state((None,None,None,1)),'interrupted')

    def test_no_connection_or_permission_is_not_evidence_of_interruption(self):
        self.cursor.execute.side_effect=RuntimeError('unavailable')
        self.assertEqual(running_state(self.job,self.execution,self.connector),'unknown')
        self.conn.close.assert_called_once()

    def test_completed_journal_does_not_need_an_additional_agent_connection(self):
        self.execution.state='completed'
        self.assertEqual(running_state(self.job,self.execution,self.connector),'completed')
        self.connector.assert_not_called()


class Interrupted2025PageTests(TestCase):
    setUp=helpers.Recalculation2025WebTests.setUp
    make_job=helpers.Recalculation2025WebTests.make_job

    def test_interrupted_state_is_visible_and_start_button_is_enabled(self):
        job=self.make_job();job.synced=True;job.save(update_fields=['synced'])
        execution=SimpleNamespace(state='running',run_id=uuid4())
        with patch('admin_motivation.services.execution_tracking.observe',return_value=execution),patch(
                'admin_motivation.services.execution_tracking.running_state',return_value='interrupted'):
            response=self.client.get(self.url+f'?job={job.pk}')
        self.assertContains(response,'Пересчёт прерван')
        self.assertContains(response,'можно запустить заново')
        self.assertNotContains(response,'disabled>Запустить пересчёт 2025')
        self.assertContains(response,f'data-recalculation-job="{job.pk}"')

    def test_active_run_stays_disabled_and_get_never_starts_a_job(self):
        job=self.make_job();job.synced=True;job.save(update_fields=['synced'])
        with patch('admin_motivation.services.execution_tracking.observe',return_value=SimpleNamespace(state='running')),patch(
                'admin_motivation.services.execution_tracking.running_state',return_value='running'),patch(
                'admin_motivation.services.calculation_jobs.start_job') as start:
            response=self.client.get(self.url+f'?job={job.pk}')
        self.assertContains(response,'disabled>Запустить пересчёт 2025')
        self.assertContains(response,'recalculation_2025_status.js');start.assert_not_called()

    def test_restart_uses_saved_inputs_and_existing_agent_activity_guard(self):
        job=self.make_job();job.synced=True;job.save(update_fields=['synced'])
        saved=job.recalculation_2025;original_rows=saved.coefficients;original_sha=saved.source_sha256
        with patch('admin_motivation.services.calculation_jobs.monitor',return_value={'status':'Отменено'}),patch(
                'admin_motivation.services.calculation_jobs.start_job') as start:
            response=self.client.post(self.url,{'action':'run','job':job.pk})
        self.assertEqual(response.status_code,302);start.assert_called_once_with(job)
        saved.refresh_from_db()
        self.assertEqual(saved.coefficients,original_rows);self.assertEqual(saved.source_sha256,original_sha)
        from .models import Motivation2025Input
        self.assertEqual(Motivation2025Input.objects.count(),1)


import os
from unittest import skipUnless
from django.contrib.staticfiles.testing import StaticLiveServerTestCase


@skipUnless(os.environ.get('RUN_BROWSER_TESTS')=='1','Optional Chromium check')
class RecalculationStatusBrowserTests(StaticLiveServerTestCase):
    make_job=helpers.Recalculation2025WebTests.make_job

    def test_running_page_refreshes_to_interrupted_and_unblocks_saved_job(self):
        from playwright.sync_api import sync_playwright,expect
        from django.conf import settings
        helpers.Recalculation2025WebTests.setUp(self)
        job=self.make_job();job.synced=True;job.save(update_fields=['synced'])
        calls=[];force_running=[False]
        def current(*args):
            calls.append(1)
            return 'running' if force_running[0] or len(calls)==1 else 'interrupted'
        def progress(job,execution,state):
            from datetime import timezone
            return {'started':datetime(2026,11,11,1,tzinfo=timezone.utc),'completed':None,
                'elapsed':3661,'ticking':state=='running','stage':'Исторические формулы 2025',
                'stage_elapsed':61,'stage_ticking':state=='running','interrupted':state=='interrupted'}
        with patch('admin_motivation.services.recalculation_2025_progress.display',side_effect=progress),patch('admin_motivation.services.execution_tracking.observe',return_value=SimpleNamespace(state='running')),patch(
                'admin_motivation.services.execution_tracking.running_state',side_effect=current),patch(
                'admin_motivation.services.calculation_jobs.start_job') as start:
            with sync_playwright() as p:
                browser=p.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE','/usr/bin/chromium'),headless=True)
                page=browser.new_page()
                page.context.add_cookies([{'name':settings.SESSION_COOKIE_NAME,'value':self.client.cookies[settings.SESSION_COOKIE_NAME].value,'url':self.live_server_url}])
                page.goto(self.live_server_url+self.url+f'?job={job.pk}')
                button=page.get_by_role('button',name='Запустить пересчёт 2025',exact=True)
                expect(button).to_be_disabled()
                expect(page.get_by_text('Исторические формулы 2025',exact=True)).to_be_visible()
                timer=page.locator('[data-elapsed-seconds="3661"]')
                expect(timer).to_have_text('1 ч 1 мин 2 сек',timeout=4000)
                expect(page.get_by_text('Пересчёт прерван. Активного задания нет; можно запустить заново',exact=False)).to_be_visible(timeout=16000)
                expect(button).to_be_enabled();start.assert_not_called()
                force_running[0]=True
                page.reload()
                page.get_by_text('Изменить сохранённые входные данные',exact=True).click()
                field=page.locator('input[name=quarter_share]');field.fill('65')
                page.get_by_role('heading',name='Пересчёт 2025',exact=True).click()
                count=len(calls)
                page.wait_for_timeout(11000)
                expect(field).to_have_value('65');self.assertEqual(len(calls),count)
                start.assert_not_called()
                browser.close()
