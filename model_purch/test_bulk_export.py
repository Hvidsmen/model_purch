from base.testing import authorize_test_case
from unittest.mock import Mock, patch

from django.contrib.messages import get_messages
from django.test import TestCase
from django.urls import reverse

from .models import ScenarioModel, Freight


class BulkScenarioExportTests(TestCase):
    def setUp(self):
        authorize_test_case(self)
        self.scenarios = [ScenarioModel.objects.create(name=f'Plan {i}', date_start_plan='2026-01-01',
                                                     date_end_plan='2026-12-31') for i in range(3)]
        self.url = reverse('bulk_export_scenarios')

    @patch('model_purch.views.MS_SQL_CONN_STR', 'test')
    @patch('model_purch.views._export_scenario_to_sql', return_value=True)
    def test_exports_only_selected_once_and_reports_count(self, export):
        ids = [self.scenarios[0].pk, self.scenarios[2].pk, self.scenarios[0].pk]
        response = self.client.post(self.url, {'scenarios': ids})
        self.assertRedirects(response, reverse('scenario_list'))
        self.assertEqual([call.args[1] for call in export.call_args_list], [self.scenarios[0], self.scenarios[2]])
        self.assertIn('Успешно: 2 из 2', ' '.join(str(m) for m in get_messages(response.wsgi_request)))

    @patch('model_purch.views.MS_SQL_CONN_STR', 'test')
    @patch('model_purch.views._export_scenario_to_sql', side_effect=[False, True])
    def test_failure_does_not_stop_remaining_scenarios(self, export):
        response = self.client.post(self.url, {'scenarios': [s.pk for s in self.scenarios[:2]]})
        self.assertEqual(export.call_count, 2)
        self.assertIn('Успешно: 1 из 2. Ошибок: 1', ' '.join(str(m) for m in get_messages(response.wsgi_request)))

    @patch('model_purch.views._export_scenario_to_sql')
    def test_invalid_selection_does_not_export_anything(self, export):
        for ids in [[], ['invalid'], [self.scenarios[0].pk, 99999]]:
            self.client.post(self.url, {'scenarios': ids})
        export.assert_not_called()
        self.assertEqual(self.client.get(self.url).status_code, 405)

    @patch('model_purch.views.MS_SQL_CONN_STR', None)
    @patch('model_purch.views._export_scenario_to_sql')
    def test_missing_connection_reports_one_error(self, export):
        response = self.client.post(self.url, {'scenarios': [s.pk for s in self.scenarios]})
        export.assert_not_called()
        self.assertEqual(len(list(get_messages(response.wsgi_request))), 1)

    @patch('model_purch.views.MS_SQL_CONN_STR', 'test')
    @patch('model_purch.views.pyodbc.connect')
    def test_real_export_flow_rolls_back_failed_scenario_then_exports_freight(self, connect):
        failed, successful = Mock(), Mock()
        failed.cursor.side_effect = RuntimeError('SQL unavailable')
        cursor = successful.cursor.return_value
        cursor.description = [('id',), ('planning_group',), ('scenario_name',)]
        cursor.fetchall.return_value = []
        connect.side_effect = [failed, successful]
        Freight.objects.create(scenario=self.scenarios[1], price_per_container='123.45', volume_per_container='67.890')
        response = self.client.post(self.url, {'scenarios': [s.pk for s in self.scenarios[:2]]})
        failed.rollback.assert_called_once()
        failed.close.assert_called_once()
        successful.rollback.assert_not_called()
        successful.close.assert_called_once()
        call = next(call for call in cursor.execute.call_args_list if 'MERGE INTO portal.Freight' in call.args[0])
        self.assertEqual(call.args[1:], (self.scenarios[1].pk, self.scenarios[1].name, '123.45', '67.890'))
        notices = ' '.join(str(m) for m in get_messages(response.wsgi_request))
        self.assertIn(self.scenarios[0].name, notices)
        self.assertIn('Успешно: 1 из 2', notices)

    def test_page_contains_selection_and_post_form(self):
        response = self.client.get(reverse('scenario_list'))
        self.assertContains(response, 'id="select-all-scenarios"')
        self.assertContains(response, 'name="scenarios"', count=3)
        self.assertContains(response, f'action="{self.url}"')
        self.assertContains(response, 'form="bulk-export-form"', count=3)
