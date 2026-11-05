from unittest.mock import MagicMock, patch
from django.test import TestCase
from django.core.exceptions import ValidationError
from .models import MotivationCalculationSchedule
from .services import calculation_jobs as jobs


class CalculationChainTests(TestCase):
    def setUp(self):
        self.job = MotivationCalculationSchedule.objects.create(
            name='Ежедневный расчёт', fact_year=2026, plan_version="Plan O'Brien")

    def test_reuses_receipt_and_copies_parameters_without_second_schedule(self):
        additional = jobs.additional_job(self.job)
        self.assertEqual(additional.pk, jobs.additional_job(self.job).pk)
        self.assertEqual(additional.variant, 'all_2026')
        for field in ('fact_year', 'plan_version', 'quarter_share', 'year_share', 'payout_threshold'):
            self.assertEqual(getattr(additional, field), getattr(self.job, field))
        self.assertFalse(additional.enabled)
        self.assertEqual(additional.mode, 'manual')
        self.assertIsNone(jobs.additional_job(additional))
        self.job.fact_year = 2025
        self.assertIsNone(jobs.additional_job(self.job))

    def test_separate_scopes_and_audit_records_in_order_under_one_lock(self):
        additional = jobs.additional_job(self.job)
        command = jobs.calculation_command(self.job, additional)
        boundary = command.index('EXEC sys.sp_executesql')
        main, extra = command[:boundary], command[boundary:]
        self.assertIn('EXEC motivation.PortalCalcBySub', main)
        self.assertIn('THROW;', main)
        self.assertIn("State_=N'completed'", main)
        self.assertIn(str(self.job.key), main)
        self.assertIn(str(additional.key), extra)
        self.assertIn('EXEC motivation.PortalCalcAll2026', extra)
        self.assertIn("Plan O''''Brien", extra)
        self.assertEqual(command.count('sys.sp_getapplock'), 1)
        self.assertEqual(command.count('DECLARE @portalRun'), 2)
        self.assertNotIn('sp_start_job', command)

    @patch.object(jobs, 'prepare')
    def test_sync_prepares_both_procedures_and_installs_single_step(self, prepare):
        connection, cursor = MagicMock(), MagicMock()
        cursor.fetchall.return_value = [(name,) for name in (
            '@FactYear', '@PlanVersion', '@QuarterShare', '@YearShare', '@PayoutThreshold')]
        cursor.fetchone.side_effect = [(1,), None, None]
        jobs.sync_job(self.job, lambda *args: (connection, cursor))
        self.assertEqual(prepare.call_args_list[0].args, (cursor,))
        self.assertEqual(prepare.call_args_list[1].args, (cursor, 'all_2026'))
        steps = [call.args for call in cursor.execute.call_args_list if 'sp_add_jobstep' in call.args[0]]
        self.assertEqual(len(steps), 1)
        self.assertIn('motivation.PortalCalcAll2026', steps[0][-1])
        connection.commit.assert_called_once()

    def test_changed_parameters_create_current_receipt(self):
        old = jobs.additional_job(self.job)
        self.job.payout_threshold = '0.9'
        changed = jobs.additional_job(self.job)
        self.assertNotEqual(old.pk, changed.pk)
        self.assertEqual(changed, MotivationCalculationSchedule.objects.filter(
            variant='all_2026').order_by('-created_at', '-pk').first())

    @patch.object(jobs, 'prepare', side_effect=[None, ValidationError('Unavailable additional procedure')])
    def test_failed_additional_preparation_preserves_agent_configuration(self, prepare):
        connection, cursor = MagicMock(), MagicMock()
        with self.assertRaisesMessage(ValidationError, 'Unavailable additional procedure'):
            jobs.sync_job(self.job, lambda *args: (connection, cursor))
        connection.rollback.assert_called_once()
        connection.commit.assert_not_called()
        self.assertFalse(any('sp_add_jobstep' in call.args[0] or 'sp_update_jobstep' in call.args[0]
                             for call in cursor.execute.call_args_list))
