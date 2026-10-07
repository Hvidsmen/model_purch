from datetime import datetime
from unittest.mock import Mock, patch
import pandas as pd
from django.test import SimpleTestCase, override_settings
from .calc_purch import calc_purch
from .conns import connection_string


class PurchaseCalculationTests(SimpleTestCase):
    def group_frames(self, qty=5, dates=True):
        return [
            pd.DataFrame({'PlanningGroupOZP': ["Group's"]}),
            pd.DataFrame({'QtyALLOrder': [qty]}),
            pd.DataFrame([[datetime(2026, 1, 1), 0, 5, "Group's", 1, 5]] if dates else [],
                         columns=['Date_', 'Количество', 'ПланСтока', 'PlanningGroupOZP', 'CoeffEnd', 'ПланПродаж']),
        ]

    def run_calc(self, frames, error=None, insert_error=None):
        connection, cursor = Mock(), Mock()
        cursor.executemany.side_effect = insert_error
        with patch('model_purch.calc_purch.connect_database', return_value=(connection, cursor)), patch(
            'model_purch.calc_purch.pd.read_sql', side_effect=frames,
        ) as read:
            if error:
                with self.assertRaises(error):
                    calc_purch()
            else:
                calc_purch()
        return connection, cursor, read

    def test_multiple_scenarios_saved_together_with_bound_parameters(self):
        scenarios = ["Scenario's 1", 'Scenario 2']
        connection, cursor, read = self.run_calc([
            pd.DataFrame({'СценарийМодели': scenarios}), *self.group_frames(), *self.group_frames(),
        ])
        self.assertEqual(cursor.executemany.call_args.args[1],
                         [("Group's", '2026-01-01', 5.0, scenario) for scenario in scenarios])
        self.assertEqual(read.call_args_list[1].kwargs['params'], [scenarios[0]])
        self.assertEqual(read.call_args_list[2].kwargs['params'], ["Group's", scenarios[0], scenarios[0]])
        self.assertNotIn(scenarios[0], read.call_args_list[1].args[0])
        connection.commit.assert_called_once()
        connection.close.assert_called_once()

    def test_no_scenarios_does_not_make_empty_bulk_insert(self):
        connection, cursor, _ = self.run_calc([pd.DataFrame({'СценарийМодели': []})])
        cursor.execute.assert_called_once_with('TRUNCATE TABLE [dbo].[PlanIncome]')
        cursor.executemany.assert_not_called()
        connection.commit.assert_called_once()
        connection.close.assert_called_once()

    def test_remainder_is_added_to_last_date(self):
        _, cursor, _ = self.run_calc([pd.DataFrame({'СценарийМодели': ['Scenario']}), *self.group_frames(qty=8)])
        self.assertEqual([row[2] for row in cursor.executemany.call_args.args[1]], [5.0, 3.0])

    def test_failed_insert_rolls_back_and_closes_connection(self):
        connection, _, _ = self.run_calc(
            [pd.DataFrame({'СценарийМодели': ['Scenario']}), *self.group_frames()],
            error=RuntimeError, insert_error=RuntimeError('insert failed'),
        )
        connection.rollback.assert_called_once()
        connection.commit.assert_not_called()
        connection.close.assert_called_once()

    def test_missing_dates_preserves_existing_output(self):
        connection, cursor, _ = self.run_calc(
            [pd.DataFrame({'СценарийМодели': ['Scenario']}), *self.group_frames(dates=False)], error=ValueError,
        )
        cursor.execute.assert_not_called()
        connection.rollback.assert_called_once()
        connection.close.assert_called_once()


class ConnectionConfigurationTests(SimpleTestCase):
    @override_settings(MS_SQL_CONN_STR='model-connection', DWH_SQL_CONN_STR='dwh-connection')
    def test_connections_use_settings(self):
        self.assertEqual(connection_string('ignored', 'ModelPurch'), 'model-connection')
        self.assertEqual(connection_string('ignored', 'DataWH'), 'dwh-connection')

    @override_settings(SETTINGS_MODULE='portal.settings_local', MS_SQL_CONN_STR=None)
    def test_local_mode_requires_sql_configuration(self):
        with self.assertRaisesRegex(RuntimeError, 'MS_SQL_CONN_STR'):
            connection_string('vm-dwh', 'ModelPurch')

    def test_legacy_modules_import_active_handlers(self):
        from . import views, views__
        from .views__ import core, scenarios, sync
        self.assertIs(views__.pggoods_list, views.pggoods_list)
        self.assertIs(core.get_current_scenario, views.get_current_scenario)
        self.assertIs(scenarios.scenario_create, views.scenario_create)
        self.assertIs(sync.export_scenario_to_sql, views.export_scenario_to_sql)
