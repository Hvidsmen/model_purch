from unittest.mock import Mock, patch

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import Client, TestCase
from django.urls import reverse

from .models import SalesPlanningChannel, SalesPlanningDirectoryLoad
from .sales_planning_source import CHANNEL_SQL, load_channels


class ChannelImportTests(TestCase):
    def import_rows(self, rows):
        connection, cursor = Mock(), Mock()
        cursor.fetchall.return_value = [(name,) for name in rows]
        with patch('base.sales_planning_source.connect_database', return_value=(connection, cursor)) as connect:
            count = load_channels()
        connect.assert_called_once_with('vm-dwh', 'DataWH')
        cursor.execute.assert_called_once_with(CHANNEL_SQL)
        cursor.close.assert_called_once()
        connection.close.assert_called_once()
        return count

    def test_repeat_import_normalizes_names_and_preserves_ids_and_missing_channels(self):
        self.assertEqual(self.import_rows([' ДПП ', 'Розница', 'дпп', None, '  ']), 2)
        first = dict(SalesPlanningChannel.objects.values_list('name', 'pk'))
        self.assertEqual(self.import_rows(['дпп', 'Опт']), 2)
        self.assertEqual(SalesPlanningChannel.objects.get(name='ДПП').pk, first['ДПП'])
        self.assertFalse(SalesPlanningChannel.objects.get(name='Розница').is_active)
        self.assertEqual(SalesPlanningDirectoryLoad.objects.get(directory='channels').row_count, 2)
        self.import_rows(['Розница'])
        self.assertEqual(SalesPlanningChannel.objects.get(name='Розница').pk, first['Розница'])

    def test_empty_invalid_or_failed_import_keeps_last_successful_snapshot(self):
        self.import_rows(['ДПП'])
        timestamp = SalesPlanningDirectoryLoad.objects.get().loaded_at
        for rows in ([], [' '], ['x' * 256]):
            with self.assertRaises(ValidationError):
                self.import_rows(rows)
        connection, cursor = Mock(), Mock()
        cursor.execute.side_effect = RuntimeError('private driver details')
        with patch('base.sales_planning_source.connect_database', return_value=(connection, cursor)):
            with self.assertRaises(RuntimeError):
                load_channels()
        cursor.close.assert_called_once()
        connection.close.assert_called_once()
        self.assertEqual(SalesPlanningDirectoryLoad.objects.get().loaded_at, timestamp)
        self.assertTrue(SalesPlanningChannel.objects.get(name='ДПП').is_active)

    def test_database_failure_rolls_back_entire_refresh(self):
        self.import_rows(['ДПП'])
        with patch('base.sales_planning_source.SalesPlanningDirectoryLoad.objects.update_or_create', side_effect=RuntimeError('fail')):
            with self.assertRaises(RuntimeError):
                self.import_rows(['Опт'])
        self.assertTrue(SalesPlanningChannel.objects.get(name='ДПП').is_active)
        self.assertFalse(SalesPlanningChannel.objects.filter(name='Опт').exists())


class ChannelPageTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser('channel-admin')
        self.client.force_login(self.user)
        self.url = reverse('sales_planning_split')

    def test_load_redirects_then_displays_saved_channels_without_dwh_on_get(self):
        def imported():
            from django.utils import timezone
            SalesPlanningChannel.objects.create(name='<script>channel</script>')
            SalesPlanningDirectoryLoad.objects.create(directory='channels', loaded_at=timezone.now(), row_count=1)
            return 1
        with patch('base.sales_planning.load_channels', side_effect=imported) as load:
            response = self.client.post(self.url, {'action': 'load_channels'}, follow=True)
        load.assert_called_once()
        self.assertContains(response, 'Каналы продаж загружены: 1')
        self.assertContains(response, '&lt;script&gt;channel&lt;/script&gt;')
        self.assertContains(response, 'МСК')

    def test_load_requires_admin_and_csrf_and_masks_connection_errors(self):
        reader = User.objects.create_user('channel-reader')
        self.client.force_login(reader)
        with patch('base.sales_planning.load_channels') as load:
            self.assertEqual(self.client.post(self.url, {'action': 'load_channels'}).status_code, 403)
            load.assert_not_called()
        secure = Client(enforce_csrf_checks=True)
        secure.force_login(self.user)
        self.assertEqual(secure.post(self.url, {'action': 'load_channels'}).status_code, 403)
        self.client.force_login(self.user)
        with patch('base.sales_planning.load_channels', side_effect=RuntimeError('password=secret')), self.assertLogs('base.sales_planning', level='ERROR'):
            response = self.client.post(self.url, {'action': 'load_channels'})
        self.assertContains(response, 'Не удалось загрузить')
        self.assertNotContains(response, 'password=secret')
