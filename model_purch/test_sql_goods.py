import json
from unittest.mock import Mock, patch

from django.db import transaction
from django.test import TestCase, SimpleTestCase
from django.urls import reverse

from .goods_identity import planning_group_key
from .models import KindPurch, PGGoods, ScenarioModel, Purch
from .sql_goods import prepare_sql_goods


class SqlGoodsPreparationTests(SimpleTestCase):
    def test_case_duplicates_are_archived_before_removal_and_latest_row_kept(self):
        cursor = Mock()
        columns = ['id', 'planning_group', 'scenario_name', 'planning_sales', 'ddp_usd']
        cursor.description = [(name,) for name in columns]
        cursor.fetchall.return_value = [
            (2245, 'Daikin SENSIRA', 'Plan', 'Old sales', 10),
            (2496, 'Daikin Sensira', 'Plan', 'New sales', 20),
            (3000, 'Daikin SENSIRA', 'Other', 'Separate sales', 30),
        ]
        prepare_sql_goods(cursor)
        calls = cursor.execute.call_args_list
        inserts = [call for call in calls if call.args[0].startswith('INSERT INTO portal.PGGoodsDuplicateArchive')]
        deletes = [call for call in calls if call.args[0].startswith('DELETE FROM portal.PGGoods')]
        updates = [call for call in calls if call.args[0].startswith('UPDATE portal.PGGoods')]
        self.assertEqual([call.args[1] for call in inserts], [2245, 2496])
        self.assertEqual([json.loads(call.args[4])['ddp_usd'] for call in inserts], [10, 20])
        self.assertTrue(all(call.args[2] == 2496 for call in inserts))
        self.assertEqual([call.args[1] for call in deletes], [2245])
        self.assertGreater(calls.index(deletes[0]), calls.index(inserts[-1]))
        self.assertEqual([call.args[2] for call in updates], [2496, 3000])
        self.assertTrue(all(call.args[1] == planning_group_key('Daikin SENSIRA') for call in updates))
        self.assertIn('CREATE UNIQUE INDEX', calls[-1].args[0])
        self.assertIn('ON portal.PGGoods (scenario_name, planning_group);', calls[-1].args[0])


class SqlGoodsExportTests(TestCase):
    def setUp(self):
        self.scenario = ScenarioModel.objects.create(name='Plan', date_start_plan='2026-01-01', date_end_plan='2026-12-31')
        Purch.objects.create(name='Supplier', lag_income=30)
        self.goods = PGGoods.objects.create(
            scenario_plan=self.scenario, planning_group='Daikin SENSIRA', planning_sales='Sales', group_goods='Group', purch='Supplier',
            kind_purch=KindPurch.objects.create(name='Purchased'), volume=1, exw_usd=2, ddp_usd=3,
            kddp=1.5, stock_cnt_day=30, percent_stock_end=20,
        )

    @patch('model_purch.views.MS_SQL_CONN_STR', 'test')
    @patch('model_purch.views.pyodbc.connect')
    def test_export_uses_same_identity_and_group_only_merge(self, connect):
        cursor, connection = Mock(), Mock()
        connection.cursor.return_value = cursor
        cursor.description = [('id',), ('planning_group',), ('scenario_name',)]
        cursor.fetchall.return_value = []
        cursor.fetchone.return_value = (501,)
        connect.return_value = connection
        response = self.client.post(reverse('export_scenario_to_sql', args=[self.scenario.pk]))
        self.assertEqual(response.status_code, 302)
        connection.rollback.assert_not_called()
        merge = next(call for call in cursor.execute.call_args_list if 'MERGE INTO portal.PGGoods AS target' in call.args[0])
        on = merge.args[0].split('ON target.')[1].split('WHEN MATCHED')[0]
        self.assertIn('planning_group_key', on)
        self.assertIn('scenario_name', on)
        self.assertNotIn('planning_sales', on)
        self.assertNotIn('group_goods', on)
        self.assertEqual(merge.args[-1], self.goods.planning_group_key)
        self.assertEqual(merge.args[0].count('?'), len(merge.args)-1)

    @patch('model_purch.views.MS_SQL_CONN_STR', 'test')
    @patch('model_purch.views.pyodbc.connect')
    def test_export_failure_rolls_back_sql_archive_and_data_changes(self, connect):
        cursor, connection = Mock(), Mock()
        connection.cursor.return_value = cursor
        cursor.description = [('id',), ('planning_group',), ('scenario_name',)]
        cursor.fetchall.return_value = []
        cursor.fetchone.return_value = (501,)
        connect.return_value = connection
        def fail_merge(sql, *args):
            if 'MERGE INTO portal.PGGoods AS target' in sql:
                raise RuntimeError('write failed')
        cursor.execute.side_effect = fail_merge
        self.client.post(reverse('export_scenario_to_sql', args=[self.scenario.pk]))
        connection.rollback.assert_called_once()
        connection.close.assert_called_once()
