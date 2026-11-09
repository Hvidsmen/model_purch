from copy import deepcopy
from decimal import Decimal
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from . import test_split_cycle
from .split_distribution_overview import overview
from .split_workflow import approve


class DistributionOverviewTests(TestCase):
    def setUp(self):
        test_split_cycle.SplitCycleTests.setUp(self)
        test_split_cycle.SplitCycleTests.complete(self)
        self.gid = str(self.group.pk)
        self.cids = [str(channel.pk) for channel in self.channels]
        self.url = reverse('split_scenario',args=[self.scenario.pk])

    def test_all_saved_channels_visible_and_filters_do_not_change_counts_or_state(self):
        original = deepcopy(self.scenario.state)
        result = overview(self.scenario,'months',{})
        self.assertEqual(len(result['rows']),2)
        self.assertEqual(result['counts']['ready'],2)
        filtered = overview(self.scenario,'months',{'channel':self.cids[1]})
        self.assertEqual(filtered['shown'],1)
        self.assertEqual(filtered['total'],2)
        self.assertEqual(filtered['rows'][0]['cid'],self.cids[1])
        self.assertEqual(overview(self.scenario,'months',{'q':'not found'})['shown'],0)
        self.assertEqual(self.scenario.state,original)

    def test_multiple_groups_and_combined_filters_keep_every_channel(self):
        extra = deepcopy(self.scenario.state['groups'][self.gid])
        extra['ref']['name'] = 'Second group'
        extra['channels'][self.cids[0]]['months']['2027-01'] = '50'
        self.scenario.state['groups']['extra'] = extra
        result = overview(self.scenario,'months',{})
        self.assertEqual(result['total'],4)
        self.assertEqual(result['counts']['ready'],3)
        self.assertEqual(result['counts']['invalid'],1)
        filtered = overview(self.scenario,'months',{'q':'second','channel':self.cids[1],'status':'ready'})
        self.assertEqual(filtered['shown'],1)
        self.assertEqual(filtered['rows'][0]['gid'],'extra')
        self.assertEqual(overview(self.scenario,'powers',{})['total'],2)

    def test_missing_partial_invalid_and_zero_plan_are_distinct(self):
        group = self.scenario.state['groups'][self.gid]
        line = group['channels'][self.cids[0]]
        line['months']['2027-01'] = None
        self.assertEqual(overview(self.scenario,'months',{})['rows'][0]['status']['key'],'missing')
        line['months']['2027-01'] = '50'
        self.assertEqual(overview(self.scenario,'months',{})['rows'][0]['status']['key'],'invalid')
        line['input'] = '0'
        self.assertEqual(overview(self.scenario,'months',{})['rows'][0]['status']['key'],'not_required')

    def test_month_quantities_must_equal_plan_and_annual_goal_uses_net_quantity(self):
        self.scenario.state['volume_mode'] = 'annual'
        line = self.scenario.state['groups'][self.gid]['channels'][self.cids[0]]
        line.update(input='30',fact='20',allocation='quantity',month_quantities={f'2027-{month:02}':'10' if month == 1 else '0' for month in range(1,13)})
        row = overview(self.scenario,'months',{})['rows'][0]
        self.assertEqual(row['quantity'],Decimal(10))
        self.assertEqual(row['status']['key'],'ready')
        line['month_quantities']['2027-02'] = '1'
        self.assertEqual(overview(self.scenario,'months',{})['rows'][0]['status']['key'],'invalid')

    def test_power_status_checks_models_and_cost_parameters_and_is_shared(self):
        power = self.scenario.state['groups'][self.gid]['powers']['07']
        power['indoor'] = None
        result = overview(self.scenario,'powers',{})
        self.assertEqual(result['total'],1)
        self.assertEqual(result['rows'][0]['status']['key'],'missing')
        months = overview(self.scenario,'months',{})['rows']
        self.assertTrue(all(row['power_status']['key'] == 'missing' for row in months))
        power['indoor'] = str(self.models[0].pk)
        power['factor'] = None
        self.assertEqual(overview(self.scenario,'powers',{})['rows'][0]['status']['key'],'missing')
        power['factor'] = '1'
        self.assertEqual(overview(self.scenario,'powers',{})['rows'][0]['status']['key'],'ready')

    def test_stock_still_requires_power_shares_even_without_sales_plan(self):
        group = self.scenario.state['groups'][self.gid]
        for line in group['channels'].values():
            line['input'] = '0'
        group['powers']['07']['share'] = None
        self.assertEqual(overview(self.scenario,'powers',{})['rows'][0]['status']['key'],'missing')

    def test_list_has_all_rows_and_never_loads_historical_source(self):
        self.scenario.state['groups'][self.gid]['channels'][self.cids[0]]['months']['2027-01'] = None
        self.scenario.save()
        with patch('base.split_distribution_history.load_basis') as source:
            response = self.client.get(self.url,{'tab':'distribution'})
            source.assert_not_called()
        self.assertContains(response,'data-distribution-row=',count=2)
        self.assertContains(response,'filter_status')
        self.assertContains(response,'Не все доли заполнены')
        self.assertNotContains(response,'name="month_2027-01"')
        self.assertContains(response,'edit=1')
        response = self.client.get(self.url,{'tab':'distribution','filter_channel':self.cids[1]})
        self.assertContains(response,'data-distribution-row=',count=1)
        with self.assertRaises(ValidationError):
            approve(self.scenario,self.actor,self.scenario.revision,False)

    def test_power_tab_lists_all_groups_once_with_all_channels(self):
        response = self.client.get(self.url,{'tab':'distribution','section':'powers'})
        self.assertContains(response,'data-distribution-row=',count=1)
        self.assertContains(response,self.channels[0].name)
        self.assertContains(response,self.channels[1].name)
