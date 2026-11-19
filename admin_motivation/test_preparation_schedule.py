from datetime import time
from unittest.mock import Mock,patch
from django.test import TestCase,SimpleTestCase
from django.urls import reverse
from base.testing import authorize_test_case
from .models import MotivationCalculationSchedule
from .operation_views import JobForm,PreparationScheduleForm
from .services import preparation_schedule as schedule,calculation_jobs


class PreparationScheduleTests(TestCase):
    def setUp(self):
        authorize_test_case(self)
        self.connection,self.cursor=Mock(),Mock()
        self.connector=Mock(return_value=(self.connection,self.cursor))

    def test_saving_time_migrates_daily_jobs_without_starting_anything(self):
        daily=MotivationCalculationSchedule.objects.create(name='Daily',mode='daily',enabled=True,synced=True)
        manual=MotivationCalculationSchedule.objects.create(name='Manual',mode='manual',synced=True)
        with patch('admin_motivation.services.prepared_sales.install') as install:
            schedule.save(time(10,35),True,self.connector)
        install.assert_called_once_with(self.cursor)
        calls=[c.args for c in self.cursor.execute.call_args_list]
        self.assertIn(('UPDATE motivation.PortalPreparationSchedule SET MoscowTime=?,Enabled=? WHERE Id=1',time(10,35),True),calls)
        inserts=[c for c in calls if c[0].startswith('INSERT motivation.PortalPreparationFollowers')]
        self.assertEqual(inserts,[(inserts[0][0],calculation_jobs.job_name(daily))])
        self.assertTrue(any('sp_detach_schedule' in c[0] and calculation_jobs.job_name(daily) in c[1:] for c in calls))
        self.assertTrue(any(c[0].startswith('DELETE FROM motivation.PortalPreparationFollowers') and c[1]==calculation_jobs.job_name(manual) for c in calls))
        self.assertFalse(any('sp_start_job' in c[0] for c in calls))
        self.connection.commit.assert_called_once();self.connection.close.assert_called_once()

    def test_failure_rolls_back_settings_and_does_not_commit(self):
        with patch('admin_motivation.services.prepared_sales.install',side_effect=RuntimeError('busy')):
            with self.assertRaises(RuntimeError):schedule.save(time(9),False,self.connector)
        self.connection.rollback.assert_called_once();self.connection.commit.assert_not_called();self.connection.close.assert_called_once()

    def test_view_validates_time_and_does_not_start_preparation(self):
        with patch.object(schedule,'save') as save,patch('admin_motivation.services.prepared_sales.start') as start:
            response=self.client.post(reverse('motivation_calculations'),{'action':'save_preparation_schedule','time':'25:00','enabled':'on'})
            self.assertEqual(response.status_code,200);save.assert_not_called()
            response=self.client.post(reverse('motivation_calculations'),{'action':'save_preparation_schedule','time':'09:40','enabled':'on'})
            self.assertEqual(response.status_code,302);save.assert_called_once_with(time(9,40),True);start.assert_not_called()
        self.assertEqual(MotivationCalculationSchedule.objects.count(),0)

    @patch.object(calculation_jobs,'prepare')
    @patch.object(calculation_jobs,'validate_sql_parameters')
    def test_daily_job_has_no_clock_and_manual_job_unsubscribes(self,*unused):
        job=MotivationCalculationSchedule.objects.create(name='Daily',mode='daily',enabled=True,fact_year=2027,plan_version='Plan')
        self.cursor.fetchone.side_effect=[(1,),None,None]
        calculation_jobs.sync_job(job,self.connector)
        calls=[c.args for c in self.cursor.execute.call_args_list]
        self.assertFalse(any('sp_add_jobschedule' in c[0] for c in calls))
        self.assertTrue(any(c[0].startswith('INSERT motivation.PortalPreparationFollowers') for c in calls))
        self.cursor.reset_mock();self.cursor.fetchone.side_effect=[(1,),None,None]
        job.mode='manual'
        calculation_jobs.sync_job(job,self.connector)
        self.assertFalse(any(c.args[0].startswith('INSERT motivation.PortalPreparationFollowers') for c in self.cursor.execute.call_args_list))


class ScheduleContractTests(SimpleTestCase):
    def test_chain_stops_on_prepare_failure_and_manual_has_no_followup(self):
        cursor=Mock();schedule.install(cursor)
        call=next(c.args for c in cursor.execute.call_args_list if '@on_success_action=3' in c.args[0])
        self.assertIn('@on_fail_action=2',call[0]);self.assertEqual(call[3],'EXEC motivation.PortalPrepareSales;')
        self.assertEqual(call[4],schedule.FOLLOWERS)
        self.assertLess(schedule.FOLLOWERS.index('sp_start_job'),schedule.FOLLOWERS.index('WHILE @Outcome IS NULL'))
        self.assertLess(schedule.FOLLOWERS.index('IF @Outcome<>1 THROW'),schedule.FOLLOWERS.rindex('FETCH NEXT'))
        detach=next(c.args for c in cursor.execute.call_args_list if 'sp_detach_schedule' in c.args[0])
        self.assertEqual(detach[1:3],('PortalMotivation_PrepareSales',)*2)

    def test_daily_form_needs_no_independent_time_and_clock_is_validated(self):
        form=JobForm({'name':'Daily','fact_year':2026,'plan_version':'v','quarter_share':80,'year_share':20,'payout_threshold':'.8','mode':'daily','enabled':'on'},plan_versions={'v':['Plan']})
        self.assertTrue(form.is_valid(),form.errors)
        self.assertTrue(PreparationScheduleForm({'time':'09:00','enabled':'on'}).is_valid())
        self.assertFalse(PreparationScheduleForm({'time':'23:70'}).is_valid())
