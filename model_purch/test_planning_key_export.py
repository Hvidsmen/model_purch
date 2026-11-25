from unittest.mock import Mock,patch
from django.test import TestCase
from django.urls import reverse
from . import test_sql_goods as fixtures
from .models import PGGoods
from .sql_goods import prepare_sql_goods
from .goods_identity import source_goods_key

class PlanningKeyExportTests(TestCase):
    setUp=fixtures.SqlGoodsExportTests.setUp

    @patch('model_purch.views.MS_SQL_CONN_STR','test')
    @patch('model_purch.views.pyodbc.connect')
    def test_explicit_planning_key_export_and_identity(self,connect):
        self.goods.source_code='PG-ERP-42';self.goods.save()
        cursor,connection=Mock(),Mock();connection.cursor.return_value=cursor
        cursor.description=[('id',),('planning_group',),('scenario_name',)]
        cursor.fetchall.return_value=[];cursor.fetchone.return_value=(501,);connect.return_value=connection
        self.client.post(reverse('export_scenario_to_sql',args=[self.scenario.pk]))
        connection.rollback.assert_not_called()
        merge=next(call for call in cursor.execute.call_args_list if 'MERGE INTO portal.PGGoods AS target' in call.args[0])
        self.assertIn('? AS PlanningKey',merge.args[0])
        self.assertEqual(merge.args[-2],'PG-ERP-42')
        self.assertEqual(merge.args[-1],source_goods_key('PG-ERP-42'))
        self.assertEqual(merge.args[1],self.goods.planning_group)
        self.assertEqual(merge.args[0].count('?'),len(merge.args)-1)
        updates=[call for call in cursor.execute.call_args_list if 'UPDATE [portal].[PGGoods]' in call.args[0]]
        self.assertTrue(any(source_goods_key('PG-ERP-42') in call.args for call in updates))

    def test_sql_preparation_preserves_distinct_erp_codes_and_empty_names(self):
        cursor=Mock();cursor.description=[('id',),('planning_group',),('scenario_name',),('PlanningKey',)]
        cursor.fetchall.return_value=[(1,'Same','Plan','A'),(2,'Same','Plan','B'),(3,'','Plan',None),(4,'','Plan',None)]
        prepare_sql_goods(cursor)
        self.assertFalse(any('DELETE FROM portal.PGGoods WHERE' in call.args[0] for call in cursor.execute.call_args_list))
        updates=[call for call in cursor.execute.call_args_list if call.args[0].startswith('UPDATE portal.PGGoods SET planning_group_key')]
        self.assertEqual(len(updates),4)
        self.assertEqual(len({call.args[1] for call in updates}),4)
        self.assertEqual(updates[0].args[1],source_goods_key('A'))

    def test_refresh_records_actual_selected_planning_key(self):
        from . import test_goods_directory as directory
        # Reuse the directory's source connector against this scenario.
        cursor,connection=Mock(),Mock();cursor.fetchall.return_value=[('Daikin SENSIRA','Sales','Group','Brand','PG-1')]
        from .services.goods_directory import refresh_goods_directory
        refresh_goods_directory(self.scenario,connector=lambda *args:(connection,cursor))
        self.goods.refresh_from_db();self.assertEqual(self.goods.source_code,'PG-1')

    def test_inspection_command_only_reads_sql_and_writes_definitions(self):
        from django.core.management import call_command
        from io import StringIO
        from tempfile import TemporaryDirectory
        from pathlib import Path
        import json
        connection,cursor=Mock(),Mock()
        cursor.fetchall.side_effect=[[('dbo','sp_ETLDataBase','SQL_STORED_PROCEDURE','CREATE PROC dbo.sp_ETLDataBase AS SELECT 1')],
            [('portal','PGGoods','PlanningKey','nvarchar',510,True)]]
        with TemporaryDirectory() as folder,patch('model_purch.management.commands.inspect_purchase_sql.connect_database',return_value=(connection,cursor)):
            output=Path(folder)/'inspect.json'
            call_command('inspect_purchase_sql',output=str(output),stdout=StringIO())
            result=json.loads(output.read_text(encoding='utf-8'))
            self.assertEqual(result['objects'][0]['name'],'sp_ETLDataBase')
        self.assertTrue(all(call.args[0].lstrip().startswith('SELECT') for call in cursor.execute.call_args_list))
        connection.commit.assert_not_called();connection.close.assert_called_once()
