from unittest.mock import patch
from uuid import uuid4
from django.test import TestCase,override_settings
from django.utils import timezone
from .models import MotivationExecution,MotivationExportLog
from .services.workflow import context


class WorkflowPeriodStatusTests(TestCase):
    def setUp(self):
        self.snapshot={'fingerprint':'current','versions':[{'id':1}]}
        MotivationExportLog.objects.create(fingerprint='current',snapshot=self.snapshot)

    def run_record(self,periods,variant='main',state='completed'):
        return MotivationExecution.objects.create(run_id=uuid4(),job_key=uuid4(),state=state,started_at=timezone.now(),
            parameters={'variant':variant,'premium_periods':periods,'premium_amount_quarters':[int(code) for code in periods if code!='5'],'quarter_catchup':False},export_snapshot=self.snapshot)

    def status(self):
        with patch('admin_motivation.services.workflow.export_snapshot',return_value=self.snapshot):return context()

    @override_settings(MOTIVATION_MAIN_ALL_QUARTERS=True)
    def test_successful_all_quarter_main_run_is_current(self):
        self.run_record(list('12345'))
        result=self.status()
        self.assertTrue(result['calculated_current'])
        self.assertEqual(result['calculation_label'],'Расчёт по текущим настройкам')

    @override_settings(MOTIVATION_MAIN_ALL_QUARTERS=True)
    def test_old_q4_only_run_still_requires_recalculation(self):
        self.run_record(['4','5'])
        self.assertFalse(self.status()['calculated_current'])

    @override_settings(MOTIVATION_MAIN_ALL_QUARTERS=False)
    def test_rollback_uses_q4_and_year(self):
        self.run_record(['4','5'])
        self.assertTrue(self.status()['calculated_current'])

    @override_settings(MOTIVATION_MAIN_ALL_QUARTERS=True)
    def test_newer_additional_variant_does_not_replace_main_status(self):
        main=self.run_record(list('12345'))
        self.run_record(['1','2','3'],variant='separate_2026',state='failed')
        result=self.status()
        self.assertEqual(result['execution'].pk,main.pk)
        self.assertTrue(result['calculated_current'])

    @override_settings(MOTIVATION_MAIN_ALL_QUARTERS=True)
    def test_failed_main_run_does_not_become_current(self):
        self.run_record(list('12345'),state='failed')
        self.assertFalse(self.status()['calculated_current'])

    @override_settings(MOTIVATION_MAIN_ALL_QUARTERS=True)
    def test_quarter_labels_without_calculated_amounts_are_not_current(self):
        run=self.run_record(list('12345'))
        run.parameters['premium_amount_quarters']=[4]
        run.save(update_fields=['parameters'])
        self.assertFalse(self.status()['calculated_current'])
