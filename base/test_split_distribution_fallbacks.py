from copy import deepcopy
from decimal import Decimal
from unittest.mock import Mock, patch
from django.core.exceptions import ValidationError
from django.test import TestCase

from . import test_split_distribution_history
from .models import SalesPlanningGoods
from .sales_planning_scope import PRODUCT_GROUP
from .split_distribution_history import resolve_proposal, load_basis


class DistributionFallbackTests(TestCase):
    def setUp(self):
        test_split_distribution_history.DistributionHistoryTests.setUp(self)

    def basis(self, scope='planning_group', *, empty=False):
        result = test_split_distribution_history.DistributionHistoryTests.basis(self)
        result['scope'] = scope
        if empty:
            result['rows'] = []
        return result

    def test_specific_channel_is_used_before_every_broader_scope(self):
        with patch('base.split_distribution_history.load_basis',return_value=self.basis()) as source:
            values,basis = resolve_proposal(self.scenario,self.group_state,'Дилеры','months')
        self.assertEqual(basis['rule_key'],'planning_group_channel')
        self.assertEqual(Decimal(values['2027-12']),50)
        source.assert_called_once()

    def test_missing_channel_uses_all_group_channels_without_reloading(self):
        with patch('base.split_distribution_history.load_basis',return_value=self.basis()) as source:
            values,basis = resolve_proposal(self.scenario,self.group_state,'Розница','months')
        self.assertEqual(basis['rule_key'],'planning_group_all_channels')
        self.assertEqual(sum(map(Decimal,values.values())),100)
        source.assert_called_once()

    def test_brand_is_used_only_after_group_and_channel_have_no_basis(self):
        def source(*args,scope,**kwargs):
            return self.basis(scope,empty=scope == 'planning_group')
        with patch('base.split_distribution_history.load_basis',side_effect=source) as mocked:
            values,basis = resolve_proposal(self.scenario,self.group_state,'Розница','months')
        self.assertEqual([call.kwargs['scope'] for call in mocked.call_args_list],['planning_group','product_brand'])
        self.assertEqual(basis['rule_key'],'product_group_brand')
        self.assertIn('Brand',basis['rule_detail'])
        self.assertEqual(Decimal(values['2027-01']),50)

    def test_product_group_is_last_and_missing_brand_is_skipped(self):
        def source(*args,scope,**kwargs):
            return self.basis(scope,empty=scope != 'product_group')
        with patch('base.split_distribution_history.load_basis',side_effect=source) as mocked:
            values,basis = resolve_proposal(self.scenario,self.group_state,'Розница','months')
        self.assertEqual([call.kwargs['scope'] for call in mocked.call_args_list],['planning_group','product_brand','product_group'])
        self.assertEqual(basis['rule_key'],'product_group')
        self.assertEqual(basis['through'],'2026-10-31')
        self.group_state['ref']['brand'] = []
        with patch('base.split_distribution_history.load_basis',side_effect=source) as mocked:
            resolve_proposal(self.scenario,self.group_state,'Розница','months')
        self.assertEqual([call.kwargs['scope'] for call in mocked.call_args_list],['planning_group','product_group'])

    def test_empty_scopes_do_not_invent_equal_distribution_or_mutate_plan(self):
        original = deepcopy(self.group_state)
        with patch('base.split_distribution_history.load_basis',return_value=self.basis(empty=True)):
            with self.assertRaisesMessage(ValidationError,'Не найдена база распределения'):
                resolve_proposal(self.scenario,self.group_state,'Дилеры','months')
        self.assertEqual(self.group_state,original)

    def test_source_failure_and_negative_weights_are_not_treated_as_missing_sales(self):
        with patch('base.split_distribution_history.load_basis',side_effect=ValidationError('Источник недоступен')) as source:
            with self.assertRaises(ValidationError):
                resolve_proposal(self.scenario,self.group_state,'Дилеры','months')
            source.assert_called_once()
        basis = self.basis()
        basis['rows'][0]['qty'] = '-10000'
        with patch('base.split_distribution_history.load_basis',return_value=basis) as source:
            with self.assertRaisesMessage(ValidationError,'Возвраты'):
                resolve_proposal(self.scenario,self.group_state,'Дилеры','months')
            source.assert_called_once()

    def test_broad_power_mix_matches_power_classes_across_models_and_excludes_other_powers(self):
        group = deepcopy(self.group_state)
        group['powers']['09'] = {'active':True,'models':[{'name':'Target nine'}]}
        for index,power in enumerate(('07','09','12')):
            SalesPlanningGoods.objects.create(source_key='fallback'+str(index),name='Other '+power,
                planning_group='Another',product_group=PRODUCT_GROUP,brand='Brand',power_class=power,block_type='Внутренний')
        def source(*args,scope,**kwargs):
            basis = self.basis(scope,empty=True)
            if scope == 'product_brand':
                basis['rows'] = [{'channel':'другой','year':2025,'month':1,'model':'other '+power,'qty':qty}
                                 for power,qty in (('07','75'),('09','25'),('12','100'))]
            return basis
        with patch('base.split_distribution_history.load_basis',side_effect=source) as mocked:
            values,basis = resolve_proposal(self.scenario,group,'Дилеры','powers')
        self.assertEqual([call.kwargs['scope'] for call in mocked.call_args_list],['planning_group','product_brand'])
        self.assertEqual({key:Decimal(value) for key,value in values.items()},{'07':75,'09':25})
        self.assertEqual(basis['rule_key'],'product_group_brand')
        self.assertIn('07, 09',basis['power_scope'])

    def test_rule_is_saved_and_visible_in_editor_and_overview(self):
        with patch('base.split_distribution_history.load_basis',return_value=self.basis()):
            response = self.client.post(self.url+'?tab=distribution&section=months',
                {'action':'fact_distribution','revision':self.scenario.revision,'group':self.gid,'channel':str(self.channels[1].pk)},follow=True)
        self.assertContains(response,'data-month-grid')
        self.assertContains(response,'Группа планирования · все каналы')
        self.scenario.refresh_from_db()
        source = self.scenario.state['groups'][self.gid]['distribution_sources']['months:'+str(self.channels[1].pk)]
        self.assertEqual(source['rule_key'],'planning_group_all_channels')
        response = self.client.get(self.url,{'tab':'distribution'})
        self.assertContains(response,'split-month-rule')
        self.assertContains(response,'Группа планирования · все каналы')

    def test_sql_scopes_are_parameterized_and_retain_stock_cutoff(self):
        connection,cursor = Mock(),Mock()
        cursor.fetchall.return_value = []
        with patch('base.split_distribution_history.connect_database',return_value=(connection,cursor)):
            load_basis(self.scenario,self.group_state,refresh=True,scope='product_brand')
            arguments = cursor.execute.call_args.args
            self.assertIn('p.BrandName',arguments[0])
            self.assertNotIn('Brand\'',arguments[0])
            self.assertEqual(arguments[1:3],('2022-01-01','2026-11-01'))
            self.assertEqual(arguments[-1],'Brand')
            load_basis(self.scenario,self.group_state,refresh=True,scope='product_group')
            self.assertNotIn('PlanningGroupOZPErp',cursor.execute.call_args.args[0])
            self.assertNotIn('p.BrandName',cursor.execute.call_args.args[0])
