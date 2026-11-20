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
        self.assertEqual(overview(self.scenario,'months',{})['rows'][0]['status']['key'],'invalid')

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
        self.assertContains(response,'data-mode-button="manual"')
        self.assertNotContains(response,'Настроить')
        response = self.client.get(self.url,{'tab':'distribution','filter_channel':self.cids[1]})
        self.assertContains(response,'data-distribution-row=',count=1)
        with self.assertRaises(ValidationError):
            approve(self.scenario,self.actor,self.scenario.revision,False)

    def test_power_tab_lists_all_groups_once_with_all_channels(self):
        response = self.client.get(self.url,{'tab':'distribution','section':'powers'})
        self.assertContains(response,'data-distribution-row=',count=1)
        self.assertContains(response,self.channels[0].name)
        self.assertContains(response,self.channels[1].name)


class DistributionChannelFilterTests(TestCase):
    def setUp(self):
        test_split_cycle.SplitCycleTests.setUp(self)
        self.url=reverse('split_scenario',args=[self.scenario.pk])
        from .models import SalesPlanningChannel
        third=SalesPlanningChannel.objects.create(name='Третий')
        cid=str(third.pk);group=next(iter(self.scenario.state['groups'].values()))
        group['channels'][cid]=deepcopy(next(iter(group['channels'].values())))
        self.scenario.state['channels'][cid]={'id':third.pk,'name':third.name}
        self.scenario.save(update_fields=['state'])
    def test_multiple_channels_empty_and_legacy_selection_preserve_whole_plan(self):
        ids=list(self.scenario.state['channels']);before=deepcopy(self.scenario.state)
        for selected in (ids[:2],ids[1:2],[]):
            response=self.client.get(self.url,{'tab':'distribution','filter_channels_set':'1','filter_channel':selected})
            self.assertEqual(response.status_code,200)
            report=response.context['distribution_overview']
            self.assertEqual({row['cid'] for row in report['rows']},set(selected))
            self.assertEqual(report['required_total'],3)
        self.assertEqual(self.client.get(self.url,{'tab':'distribution'}).context['distribution_overview']['shown'],3)
        self.assertEqual(self.client.get(self.url,{'tab':'distribution','filter_channel':''}).context['distribution_overview']['shown'],3)
        self.assertEqual(self.client.get(self.url,{'tab':'distribution','filter_channel':ids[0]}).context['distribution_overview']['shown'],1)
        self.scenario.refresh_from_db();self.assertEqual(self.scenario.state,before)


import os,importlib.util
from unittest import skipUnless
from django.contrib.staticfiles.testing import StaticLiveServerTestCase

@skipUnless(os.environ.get('RUN_BROWSER_TESTS')=='1' and importlib.util.find_spec('playwright'),'Requires Chromium')
class DistributionChannelFilterBrowserTests(StaticLiveServerTestCase):
    def setUp(self):
        DistributionChannelFilterTests.setUp(self)
        for group in self.scenario.state['groups'].values():
            for line in group['channels'].values():line['distribution_mode']='manual'
        self.scenario.save(update_fields=['state'])
    def test_multiple_channel_filter_saves_pending_draft_before_navigation(self):
        from playwright.sync_api import sync_playwright,expect
        with sync_playwright() as driver:
            browser=driver.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'),headless=True)
            context=browser.new_context(viewport={'width':1700,'height':1100})
            context.add_cookies([{'name':'sessionid','value':self.client.cookies['sessionid'].value,'url':self.live_server_url}])
            page=context.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            try:
                page.goto(self.live_server_url+self.url+'?tab=distribution')
                expect(page.locator('[data-grid-row]')).to_have_count(3)
                page.locator('[data-grid-row]').first.locator('[data-month]').first.fill('100')
                picker=page.locator('[data-distribution-channel-picker]');picker.locator('summary').click()
                picker.locator('[data-distribution-channel-search]').fill('Третий')
                third=picker.locator('[name=filter_channel]').last;third.uncheck()
                picker.locator('summary').click()
                page.get_by_role('button',name='Показать',exact=True).click()
                expect(page.locator('.split-unsaved-dialog')).to_be_visible()
                with page.expect_navigation():page.get_by_role('button',name='Сохранить и перейти',exact=True).click()
                expect(page.locator('[data-grid-row]')).to_have_count(2)
                expect(picker.locator('[data-distribution-channel-caption]')).to_have_text('Выбрано: 2')
                self.assertEqual(Decimal(page.locator('[data-grid-row]').first.locator('[data-month]').first.input_value().replace(',','.')),100)
                picker.locator('summary').click();picker.locator('[data-distribution-channels-none]').click();picker.locator('summary').click()
                with page.expect_navigation():page.get_by_role('button',name='Показать',exact=True).click()
                expect(page.locator('[data-grid-row]')).to_have_count(0)
                expect(picker.locator('[data-distribution-channel-caption]')).to_have_text('Не выбраны')
                self.assertEqual(errors,[])
            finally:context.close();browser.close()
