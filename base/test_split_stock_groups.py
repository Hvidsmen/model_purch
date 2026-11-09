from copy import deepcopy
from datetime import date
from unittest.mock import Mock, patch
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from . import test_split_cycle
from .models import SalesPlanningGoods, SalesPlanningGroup
from .sales_planning_group_service import rebuild_groups
from .split_stock_groups import groups_from_stock, SNAPSHOT_SQL, PRESENCE_SQL
from .split_workflow import changed_state, approve, persist
from .sales_planning_scope import PRODUCT_GROUP


class StockGroupSelectionTests(TestCase):
    def setUp(self):
        test_split_cycle.SplitCycleTests.setUp(self)
        test_split_cycle.SplitCycleTests.complete(self)
        SalesPlanningGoods.objects.create(source_key='new', name='New model', product_group=PRODUCT_GROUP,
            planning_group='Transit only', brand='Brand', power_class='09', block_type='Наружный')
        SalesPlanningGoods.objects.create(source_key='foreign', name='Foreign', product_group='Вентиляция',
            planning_group='Foreign', power_class='09', block_type='Наружный')
        rebuild_groups()

    def provider(self, rows, count=10):
        connection, cursor = Mock(), Mock()
        cursor.fetchone.return_value = (count,)
        cursor.fetchall.return_value = rows
        return connection, cursor

    def test_source_uses_actual_stock_date_union_of_stock_and_transit_and_domestic_scope(self):
        connection, cursor = self.provider([('G', 3, 0), ('G', 0, 5), ('Transit only', 0, 2), ('Transit only', -3, 1), ('G', 0, 0)])
        with patch('base.split_stock_groups.connect_database', return_value=(connection, cursor)) as connect:
            result = groups_from_stock(date(2026,12,31))
        connect.assert_called_once_with('vm-dwh','DataWH')
        self.assertEqual(cursor.execute.call_args_list[0].args, (SNAPSHOT_SQL, '2026-12-31'))
        self.assertEqual(cursor.execute.call_args_list[1].args, (PRESENCE_SQL, '2026-12-31', PRODUCT_GROUP))
        self.assertEqual(len(result['groups']), 2)
        original = next(row for row in result['groups'] if row['name']=='G')
        self.assertTrue(original['stock'])
        self.assertTrue(original['transit'])
        self.assertTrue(next(row for row in result['groups'] if row['name']=='Transit only')['transit'])
        cursor.close.assert_called_once()
        connection.close.assert_called_once()

    def test_missing_snapshot_unmapped_groups_and_invalid_quantities_do_not_report_empty_success(self):
        for rows, count in [([],0), ([('Missing',1,0)],1), ([('',1,0)],1), ([('G','NaN',0)],1)]:
            connection, cursor = self.provider(rows, count)
            with patch('base.split_stock_groups.connect_database', return_value=(connection,cursor)):
                with self.assertRaises(ValidationError):
                    groups_from_stock(self.scenario.stock_date)
        connection,cursor = self.provider([])
        with patch('base.split_stock_groups.connect_database', return_value=(connection,cursor)):
            self.assertEqual(groups_from_stock(self.scenario.stock_date)['groups'], [])

    def test_addition_preserves_existing_inputs_and_does_not_select_channels_or_rewrite_stock(self):
        other = SalesPlanningGroup.objects.get(name='Transit only')
        inventory = {'date':str(self.scenario.stock_date), 'groups':[{'id':str(other.pk),'name':other.name,'stock':False,'transit':True}]}
        original = deepcopy(self.scenario.state)
        with patch('base.split_stock_groups.groups_from_stock', return_value=inventory):
            state = changed_state(self.scenario, {'action':'add_stock_groups'})
        self.assertEqual({key:state['groups'][str(self.group.pk)][key] for key in original['groups'][str(self.group.pk)]}, original['groups'][str(self.group.pk)])
        self.assertEqual(state['channels'], original['channels'])
        self.assertEqual(state['inventory_selection']['added'], 1)
        self.assertIsNone(state['groups'][str(other.pk)]['ddp'])
        self.assertTrue(all(line['input']=='0' for line in state['groups'][str(other.pk)]['channels'].values()))
        self.assertEqual(self.scenario.state, original)
        self.scenario.state = state
        with patch('base.split_stock_groups.groups_from_stock', return_value=inventory):
            again = changed_state(self.scenario, {'action':'add_stock_groups'})
        self.assertEqual(again['inventory_selection']['added'], 0)
        self.assertEqual(len(again['groups']), 2)

    def test_endpoint_saves_inline_edits_and_approved_plan_does_not_query_source(self):
        gid, cid = str(self.group.pk), str(self.channels[0].pk)
        url = reverse('split_scenario',args=[self.scenario.pk])
        post = {'action':'add_stock_groups','revision':self.scenario.revision,'with_lines':'1','ddp_'+gid:'100'}
        for key,line in self.scenario.state['groups'][gid]['channels'].items():
            post.update({'q_'+gid+'_'+key:line['input'], 'markup_'+gid+'_'+key:line['markup'], 'fact_'+gid+'_'+key:''})
        post['q_'+gid+'_'+cid] = '21'
        with patch('base.split_stock_groups.groups_from_stock', return_value={'date':str(self.scenario.stock_date),'groups':[]}):
            self.assertEqual(self.client.post(url,post).status_code,302)
        self.scenario.refresh_from_db()
        self.assertEqual(self.scenario.state['groups'][gid]['channels'][cid]['input'],'21')
        approve(self.scenario,self.actor,self.scenario.revision,False)
        self.scenario.refresh_from_db()
        with patch('base.split_stock_groups.groups_from_stock') as source:
            self.client.post(url,{'action':'add_stock_groups','revision':self.scenario.revision})
            source.assert_not_called()

    def test_connection_failure_is_safe_and_does_not_change_scenario(self):
        url = reverse('split_scenario',args=[self.scenario.pk])
        state = deepcopy(self.scenario.state)
        with patch('base.split_stock_groups.connect_database', side_effect=RuntimeError('password=secret')), self.assertLogs('base.split_stock_groups',level='ERROR'):
            response = self.client.post(url,{'action':'add_stock_groups','revision':self.scenario.revision})
        self.assertContains(response,'Не удалось получить сток')
        self.assertNotContains(response,'password=secret')
        self.scenario.refresh_from_db()
        self.assertEqual(self.scenario.state,state)
