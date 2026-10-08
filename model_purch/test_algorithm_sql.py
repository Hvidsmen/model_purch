from base.testing import authorize_test_case
from unittest.mock import Mock, patch

from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from .conns import connection_string
from .models import AlgorithmRun, AlgorithmStep
from .views import step_2_prepare_data, step_4_update_tables, step_5_olap_cube


class AlgorithmSqlTests(SimpleTestCase):
    @override_settings(SETTINGS_MODULE='portal.settings_local', MS_SQL_CONN_STR=None)
    @patch('model_purch.conns.pyodbc.connect')
    def test_missing_connection_fails_with_actionable_message_before_odbc_call(self, connect):
        for step in [step_2_prepare_data, step_4_update_tables, step_5_olap_cube]:
            with self.assertRaisesRegex(RuntimeError, 'задайте MS_SQL_CONN_STR'):
                step(1)
        connect.assert_not_called()

    @override_settings(MS_SQL_CONN_STR=('invalid',))
    def test_non_string_connection_reports_configuration_error(self):
        with self.assertRaisesRegex(RuntimeError, 'должна быть строкой'):
            connection_string('vm-dwh', 'ModelPurch')

    @override_settings(MS_SQL_CONN_STR='configured-connection')
    @patch('model_purch.conns.pyodbc.connect')
    def test_all_steps_use_runtime_settings_commit_and_close(self, connect):
        for step in [step_2_prepare_data, step_4_update_tables, step_5_olap_cube]:
            connection, cursor = Mock(), Mock()
            connection.cursor.return_value = cursor
            cursor.nextset.side_effect = [True, False]
            connect.return_value = connection
            step(1)
            connect.assert_called_with('configured-connection')
            connection.commit.assert_called_once()
            connection.close.assert_called_once()
            connection.rollback.assert_not_called()

    @patch('model_purch.views.connect_database')
    def test_error_in_later_procedure_rolls_back_and_closes(self, connect):
        connection, cursor = Mock(), Mock()
        cursor.nextset.side_effect = RuntimeError('second procedure failed')
        connect.return_value = connection, cursor
        with self.assertRaisesRegex(RuntimeError, 'second procedure failed'):
            step_2_prepare_data(1)
        connection.rollback.assert_called_once()
        connection.commit.assert_not_called()
        connection.close.assert_called_once()


class AlgorithmConfigurationApiTests(TestCase):
    def setUp(self):
        authorize_test_case(self)

    @override_settings(SETTINGS_MODULE='portal.settings_local', MS_SQL_CONN_STR=None)
    @patch('model_purch.conns.pyodbc.connect')
    def test_preparation_records_configuration_failure_in_step_and_run(self, connect):
        run = AlgorithmRun.objects.create(status='running')
        step = AlgorithmStep.objects.create(algorithm_run=run, order=2, name='Подготовка данных')
        response = self.client.post(reverse('execute_next_step_api', args=[run.pk]))
        self.assertEqual(response.status_code, 500)
        self.assertIn('задайте MS_SQL_CONN_STR', response.json()['error'])
        step.refresh_from_db()
        run.refresh_from_db()
        self.assertEqual(step.status, 'failed')
        self.assertEqual(run.status, 'failed')
        self.assertIn('задайте MS_SQL_CONN_STR', step.error_message)
        connect.assert_not_called()
