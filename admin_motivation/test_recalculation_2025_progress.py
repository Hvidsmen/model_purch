from datetime import datetime,timedelta,timezone
from types import SimpleNamespace
from uuid import uuid4
from unittest.mock import Mock,patch
from django.test import SimpleTestCase,TestCase
from django.core.exceptions import ValidationError
from .services.recalculation_2025_progress import display,patch as instrument,STAGES,stage_sql,begin_sql,finish_sql
from .services.recalculation_2025_sql import prepare
from .services.execution_tracking import tracked_statement
from . import test_recalculation_2025 as helpers


class StageInstrumentationTests(SimpleTestCase):
    def source(self):
        cursor=Mock();cursor.fetchone.side_effect=[(None,),(0,)]
        prepare(cursor)
        return cursor.execute.call_args.args[0]

    def test_instrumentation_keeps_financial_sql_and_transaction_order(self):
        source=self.source()
        positions=[source.index('-- Portal 2025 progress: '+label) for label in STAGES]
        self.assertEqual(positions,sorted(positions))
        original=source
        for label in STAGES:original=original.replace(stage_sql(label),'',1)
        self.assertNotIn('Portal 2025 progress:',original)
        self.assertEqual(instrument(original),source)
        self.assertEqual(instrument(source),source)
        self.assertLess(source.index('Исторические формулы 2025'),source.index('BEGIN TRANSACTION;'))
        self.assertLess(source.index('Сохранение результатов'),source.index('COMMIT TRANSACTION;'))
        self.assertIn('Price - AmountUSDIt_ITW + BallsOuterUSD',source)
        self.assertNotIn('READUNCOMMITTED',source)

    def test_unknown_phase_shape_fails_before_installing_procedure(self):
        source=self.source()
        for label in STAGES:source=source.replace(stage_sql(label),'',1)
        with self.assertRaisesMessage(ValidationError,'граница этапа'):
            instrument(source.replace('COMMIT TRANSACTION;',''))

    def test_wrapper_records_failed_stage_after_rollback_not_fake_completion(self):
        job=SimpleNamespace(key=uuid4())
        with patch('admin_motivation.services.execution_tracking.parameters',return_value={'variant':'test_2025'}):
            sql=tracked_statement(job,'EXEC example;')
        self.assertIn(begin_sql(),sql)
        catch=sql[sql.index('BEGIN CATCH'):]
        self.assertLess(catch.index('ROLLBACK'),catch.index(finish_sql(failed=True)))
        self.assertLess(catch.index(finish_sql(failed=True)),catch.index("State_=N'failed'"))
        self.assertIn('SESSION_CONTEXT',catch);self.assertIn('THROW;',catch)

    def test_other_variants_do_not_get_2025_stage_writes(self):
        with patch('admin_motivation.services.execution_tracking.parameters',return_value={'variant':'main'}):
            sql=tracked_statement(SimpleNamespace(key=uuid4()),'EXEC example;')
        self.assertNotIn('PortalRecalculation2025',sql)


class ProgressDisplayTests(SimpleTestCase):
    def setUp(self):
        self.started=datetime(2026,11,11,1,tzinfo=timezone.utc)
        self.now=self.started+timedelta(hours=2)
        self.job=SimpleNamespace(key=uuid4())
        self.run=SimpleNamespace(run_id=uuid4(),started_at=self.started,completed_at=None)
        self.conn=Mock();self.cursor=Mock();self.connector=Mock(return_value=(self.conn,self.cursor))
        self.cursor.fetchone.return_value=('Исторические формулы 2025',self.started+timedelta(minutes=5),None)

    def get(self,state='running'):
        return display(self.job,self.run,state,connector=self.connector,now=self.now)

    def test_running_elapsed_stage_and_explicit_moscow_start(self):
        result=self.get()
        self.assertEqual(result['started'].hour,4)
        self.assertEqual(result['elapsed'],7200);self.assertEqual(result['stage_elapsed'],6900)
        self.assertTrue(result['ticking']);self.assertTrue(result['stage_ticking'])
        sql=self.cursor.execute.call_args_list[0].args[0]
        self.assertIn('WITH (READUNCOMMITTED)',sql)
        self.assertIn('p.RunId=? AND r.JobKey=?',sql)
        self.assertNotIn('UPDATE',sql);self.conn.close.assert_called_once()

    def test_completed_and_failed_durations_freeze_at_recorded_end(self):
        end=self.started+timedelta(minutes=20)
        self.cursor.fetchone.return_value=('Расчёт завершён',self.started+timedelta(minutes=19),end)
        for state in ('completed','failed'):
            result=self.get(state)
            self.assertEqual(result['elapsed'],1200)
            self.assertFalse(result['ticking']);self.assertFalse(result['stage_ticking'])

    def test_legacy_running_job_still_has_time_without_inventing_phase(self):
        self.cursor.fetchone.return_value=None
        result=self.get()
        self.assertEqual(result['elapsed'],7200);self.assertEqual(result['stage'],'')

    def test_connection_failure_preserves_elapsed_and_closes(self):
        self.cursor.execute.side_effect=RuntimeError('Unavailable')
        result=self.get()
        self.assertEqual(result['elapsed'],7200);self.assertEqual(result['stage'],'')
        self.conn.close.assert_called_once()

    def test_naive_sql_timestamps_are_utc_and_negative_clock_delta_clamped(self):
        self.run.started_at=self.started.replace(tzinfo=None)
        self.run.completed_at=(self.started-timedelta(seconds=1)).replace(tzinfo=None)
        self.cursor.fetchone.return_value=None
        self.assertEqual(self.get('completed')['elapsed'],0)

    def test_interrupted_never_keeps_timer_ticking(self):
        result=self.get('interrupted')
        self.assertTrue(result['interrupted']);self.assertFalse(result['ticking'])
        self.assertIsNone(result['completed'])

    def test_no_execution_makes_no_remote_query(self):
        self.assertIsNone(display(self.job,None,'unknown',connector=self.connector))
        self.connector.assert_not_called()


class ProgressPageTests(TestCase):
    setUp=helpers.Recalculation2025WebTests.setUp
    make_job=helpers.Recalculation2025WebTests.make_job

    def test_running_page_displays_time_stage_and_never_starts(self):
        job=self.make_job();job.synced=True;job.save(update_fields=['synced'])
        started=datetime(2026,11,11,4,tzinfo=timezone(timedelta(hours=3)))
        progress={'started':started,'completed':None,'elapsed':3661,'ticking':True,
            'stage':'Исторические формулы 2025','stage_elapsed':61,'stage_ticking':True}
        with patch('admin_motivation.services.execution_tracking.observe'),patch(
                'admin_motivation.services.execution_tracking.running_state',return_value='running'),patch(
                'admin_motivation.services.recalculation_2025_progress.display',return_value=progress),patch(
                'admin_motivation.services.calculation_jobs.start_job') as start:
            response=self.client.get(self.url+f'?job={job.pk}')
        self.assertContains(response,'11.11.2026 04:00:00 МСК')
        self.assertContains(response,'Исторические формулы 2025')
        self.assertContains(response,'data-elapsed-seconds="3661"')
        self.assertContains(response,'data-elapsed-ticking="true"')
        self.assertContains(response,'disabled>Запустить пересчёт 2025');start.assert_not_called()
