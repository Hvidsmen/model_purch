from datetime import datetime
from pathlib import Path
import tempfile
from unittest.mock import patch
from zoneinfo import ZoneInfo
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.test import SimpleTestCase, TestCase, override_settings
from django.contrib.auth.models import Group, User
from django.urls import reverse
from .services import snapshots
from .tests import fixture
from base.access import ADMIN, ANALYST, ensure_roles
from base.models import UserProjectAccess


class SnapshotTests(SimpleTestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.settings_override = override_settings(DEALER_SNAPSHOT_DIR=temp.name)
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        cache.clear()
        self.source = {'rows': fixture(), 'loaded_at': '2026-10-24T10:00:00+00:00', 'end_exclusive': '2026-10-01'}

    def test_persistent_snapshot_is_read_without_any_sql_and_survives_cache_clear(self):
        with patch('dashboards.services.snapshots.fetch_sales', return_value=self.source) as fetch:
            self.assertTrue(snapshots.run_refresh())
            fetch.assert_called_once()
            self.assertTrue(fetch.call_args.kwargs['force'])
        for attempt in range(2):
            cache.clear()
            with patch('dashboards.services.source.connect_database') as connect:
                result = snapshots.read_snapshot()
                self.assertEqual(result['rows'][0]['quantity'], fixture()[0]['quantity'])
                self.assertEqual(result['rows'][0]['month'], fixture()[0]['month'])
                connect.assert_not_called()
        self.assertEqual(snapshots.status()['run']['state'], 'completed')
        self.assertEqual(len(snapshots.read_json('history.json')), 1)

    def test_failed_refresh_keeps_last_successful_snapshot_and_history(self):
        with patch('dashboards.services.snapshots.fetch_sales', return_value=self.source):
            self.assertTrue(snapshots.run_refresh())
        before = snapshots.read_json('current.json')
        with patch('dashboards.services.snapshots.fetch_sales', side_effect=RuntimeError('SQL unavailable')):
            self.assertFalse(snapshots.run_refresh())
        self.assertEqual(snapshots.read_json('current.json'), before)
        self.assertEqual(snapshots.read_snapshot()['refresh_status']['state'], 'error')
        self.assertEqual(len(snapshots.read_json('history.json')), 2)

    def test_concurrent_job_is_rejected_and_lock_is_released_after_failure(self):
        handle = snapshots.acquire_lock()
        self.assertIsNotNone(handle)
        try:
            with patch('dashboards.services.snapshots.fetch_sales') as fetch:
                self.assertFalse(snapshots.run_refresh())
                self.assertFalse(snapshots.start_refresh())
                fetch.assert_not_called()
        finally:
            snapshots.release_lock(handle)
        next_handle = snapshots.acquire_lock()
        self.assertIsNotNone(next_handle)
        snapshots.release_lock(next_handle)

    def test_crashed_job_is_detected_and_does_not_block_manual_retry(self):
        snapshots.write_json('run.json', {'state': 'running', 'started_at': '2026-10-24T10:00:00+00:00', 'rows': 0})
        self.assertEqual(snapshots.status()['run']['state'], 'interrupted')
        with patch('dashboards.services.snapshots.fetch_sales', return_value=self.source):
            self.assertTrue(snapshots.run_refresh())
        self.assertEqual(snapshots.status()['run']['state'], 'completed')

    def test_schedule_stays_disabled_until_time_is_supplied_and_uses_moscow(self):
        self.assertFalse(snapshots.schedule()['enabled'])
        with self.assertRaises(ValidationError): snapshots.save_schedule(True, '')
        with self.assertRaises(ValidationError): snapshots.save_schedule(True, '25:61')
        config = snapshots.save_schedule(True, '08:00')
        now = datetime(2026, 10, 24, 8, 1, tzinfo=ZoneInfo('Europe/Moscow'))
        self.assertTrue(snapshots.due(now, config, {}))
        self.assertFalse(snapshots.due(now, config, {'started_at': now.isoformat()}))
        self.assertFalse(snapshots.due(now, {'enabled': False, 'time': ''}, {}))

    def test_no_snapshot_is_explicit_error_and_retention_keeps_two_good_files(self):
        with self.assertRaisesMessage(ValidationError, 'Данные ещё не подготовлены'):
            snapshots.read_snapshot()
        with patch('dashboards.services.snapshots.fetch_sales', return_value=self.source):
            for attempt in range(3): self.assertTrue(snapshots.run_refresh())
        self.assertEqual(len(list(snapshots.directory().glob('data-*.json.gz'))), 2)
        self.assertTrue((snapshots.directory() / snapshots.read_json('current.json')['file']).is_file())


class SnapshotJobAccessTests(TestCase):
    def setUp(self):
        ensure_roles()
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.settings_override = override_settings(DEALER_SNAPSHOT_DIR=temp.name)
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        self.user = User.objects.create_user('snapshot-role-user')

    def test_analyst_observes_but_only_project_admin_can_run_and_schedule(self):
        UserProjectAccess.objects.create(user=self.user, project='dashboards', role=ANALYST)
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(reverse('dashboard_refresh')).status_code, 200)
        self.assertEqual(self.client.get(reverse('dashboard_refresh_status')).status_code, 200)
        for action in ('run', 'schedule'):
            self.assertEqual(self.client.post(reverse('dashboard_refresh'), {'action': action}).status_code, 403)
        UserProjectAccess.objects.filter(user=self.user).update(role=ADMIN)
        with patch('dashboards.services.snapshots.start_refresh', return_value=True) as start:
            response = self.client.post(reverse('dashboard_refresh'), {'action': 'run'}, HTTP_ACCEPT='application/json')
            self.assertEqual(response.status_code, 202)
            start.assert_called_once()
        self.assertEqual(self.client.post(reverse('dashboard_refresh'), {'action': 'schedule', 'enabled': 'on', 'time': '08:00'}).status_code, 302)
        self.assertEqual(snapshots.schedule()['time'], '08:00')
        self.assertFalse(self.user.is_staff)
