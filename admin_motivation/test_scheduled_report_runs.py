import json
from datetime import datetime, timezone
from uuid import uuid4
from unittest.mock import Mock, patch
from django.core.exceptions import ValidationError
from django.test import TestCase
from .models import MotivationExecution
from .services.execution_tracking import observe_latest, latest_for_report


class ScheduledReportRunTests(TestCase):
    def setUp(self):
        self.run_id, self.job_key = uuid4(), uuid4()
        self.connection, self.cursor = Mock(), Mock()
        self.cursor.fetchone.return_value = (self.run_id, self.job_key, datetime(2026, 11, 5, 6),
            datetime(2026, 11, 5, 6, 15), 'completed', json.dumps({'variant': 'main', 'fact_year': 2026}),
            json.dumps({'versions': []}))
        self.connector = Mock(return_value=(self.connection, self.cursor))

    def test_scheduled_run_is_read_even_without_local_record_and_is_not_persisted_for_reader(self):
        run = observe_latest('main', self.connector)
        self.assertEqual(run.run_id, self.run_id)
        self.assertEqual(run.state, 'completed')
        self.assertEqual(run.started_at, datetime(2026, 11, 5, 6, tzinfo=timezone.utc))
        self.assertEqual(run.parameters['fact_year'], 2026)
        self.assertEqual(run.export_snapshot, {'versions': []})
        self.assertEqual(MotivationExecution.objects.count(), 0)
        query, variant = self.cursor.execute.call_args.args
        self.assertEqual(variant, 'main')
        self.assertIn("COALESCE(JSON_VALUE(Parameters_,'$.variant'),N'main')=?", query)
        self.assertIn('ORDER BY StartedAt DESC', query)
        self.assertNotIn("State_=N'completed'", query)
        self.connection.close.assert_called_once()

    def test_admin_can_synchronize_scheduled_audit_to_portal(self):
        run = observe_latest('main', self.connector, persist=True)
        self.assertEqual(MotivationExecution.objects.get(run_id=self.run_id).pk, run.pk)
        self.assertEqual(MotivationExecution.objects.count(), 1)
        observe_latest('main', self.connector, persist=True)
        self.assertEqual(MotivationExecution.objects.count(), 1)

    def test_additional_variant_is_filtered_independently(self):
        observe_latest('all_2026', self.connector)
        self.assertEqual(self.cursor.execute.call_args.args[-1], 'all_2026')
        with self.assertRaises(ValidationError):
            observe_latest('other', self.connector)

    def test_unavailable_journal_keeps_fallback_for_shared_lock_validation(self):
        fallback = Mock()
        self.cursor.fetchone.return_value = None
        self.assertIsNone(observe_latest('main', self.connector))
        with patch('admin_motivation.services.execution_tracking.observe_latest', return_value=None):
            self.assertIs(latest_for_report(fallback), fallback)

    def test_failed_or_running_audit_never_returns_previous_success(self):
        for state in ('running', 'failed'):
            with self.subTest(state=state), patch('admin_motivation.services.execution_tracking.observe_latest', return_value=Mock(state=state)):
                with self.assertRaises(ValidationError):
                    latest_for_report(Mock(state='completed'))

    def test_shared_lock_guard_still_rejects_other_run(self):
        from .services.reports_next import current_run
        execution = Mock(run_id=self.run_id, parameters={'variant': 'main'})
        self.cursor.fetchone.side_effect = [(0,), (uuid4(), 'completed')]
        with self.assertRaises(ValidationError):
            with current_run(execution, self.connector):
                self.fail('Mismatched table must not be read')
        self.assertIn('sp_releaseapplock', self.cursor.execute.call_args.args[0])
