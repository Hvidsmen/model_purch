from copy import deepcopy
from datetime import date
from decimal import Decimal as D
import importlib.util,json,os
from unittest import skipUnless
from unittest.mock import patch
from django.test import TestCase
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from . import test_split_forecast_history as fixtures
from .split_sales_prefill import calculate,source,versions
from .split_history import normalized


def setup(test):
    fixtures.HistoricalForecastTests.setUp(test)
    group=test.scenario.state['groups'][test.gid];group.update(forecast_mode='group',forecast_before_total='30')
    for line in group['channels'].values():line.update(input='0',markup=None)
    test.scenario.save(update_fields=['state'])
    test.records={}
    for index,(cid,ch) in enumerate(test.scenario.state['channels'].items(),1):
        for power in group['powers'].values():
            for kind in ('indoor','outdoor'):
                model=next(m for m in power['models'] if m['id']==power[kind])
                qty=D(10*index)
                test.records[(group['ref']['source_key'],normalized(ch['name']),normalized(model['name']))]={'qty':qty,'cost':qty*10,'revenue':qty*15,'unknown':False}
    test.params={'method':'history_forecast','year':'2026','quantity_coefficient':'1.1','markup_coefficient':'1.1','fill':'replace',
                 'groups':json.dumps([test.gid]),'channels':json.dumps(list(test.scenario.state['channels']))}


class SalesPrefillTests(TestCase):
    setUp=setup
    def run_fill(self,**overrides):
        with patch('base.split_sales_prefill.source',return_value=(self.records,{'method':'test','year':2026})):
            return calculate(self.scenario,{**self.params,**overrides})
    def test_history_plus_saved_forecast_allocates_channels_and_preserves_other_inputs(self):
        before=deepcopy(self.scenario.state);state,result=self.run_fill()
        self.assertEqual(result['count'],2);self.assertFalse(result['skipped'])
        lines=list(state['groups'][self.gid]['channels'].values())
        self.assertEqual([D(line['input']) for line in lines],[D(33),D(66)])
        self.assertTrue(all(D(line['markup'])==D('1.65') for line in lines))
        self.assertEqual(state['groups'][self.gid]['ddp'],before['groups'][self.gid]['ddp'])
        self.assertEqual(state['groups'][self.gid]['buy'],before['groups'][self.gid]['buy'])
        self.assertEqual(state['groups'][self.gid]['powers'],before['groups'][self.gid]['powers'])
        self.assertEqual(self.scenario.state,before)
    def test_erp_uses_source_quantity_and_markup_without_adding_forecast(self):
        state,result=self.run_fill(method='erp',version='Plan 2026')
        self.assertEqual([D(line['input']) for line in state['groups'][self.gid]['channels'].values()],[D(22),D(44)])
        self.assertEqual(result['count'],2)
    def test_append_locks_and_missing_forecast_keep_values(self):
        group=self.scenario.state['groups'][self.gid];cids=list(group['channels'])
        group['channels'][cids[0]].update(input='7',markup='2',lock_qty=True,lock_markup=True)
        state,result=self.run_fill(fill='append')
        self.assertEqual(state['groups'][self.gid]['channels'][cids[0]]['input'],'7')
        group['forecast_before_total']=None
        state,result=self.run_fill()
        self.assertEqual(state['groups'][self.gid]['channels'][cids[1]]['input'],'0')
        self.assertEqual(D(state['groups'][self.gid]['channels'][cids[1]]['markup']),D('1.65'))
        self.assertTrue(any('шаге 4' in row['reason'] for row in result['skipped']))
    def test_save_skips_bad_group_and_checks_revision(self):
        other=deepcopy(self.scenario.state['groups'][self.gid]);other['ref'].update(name='Missing',source_key='missing')
        self.scenario.state['groups']['other']=other;self.scenario.save(update_fields=['state'])
        payload={'action':'prefill_sales_plan','revision':self.scenario.revision,**self.params,'groups':json.dumps([self.gid,'other'])}
        with patch('base.split_sales_prefill.source',return_value=(self.records,{})):
            response=self.client.post(self.url,payload)
            self.assertEqual(response.status_code,200,response.content)
            self.assertEqual(response.json()['count'],2)
            self.assertEqual(self.client.post(self.url,payload).status_code,400)
        self.scenario.refresh_from_db();self.assertEqual(self.scenario.state['groups']['other'],other)
    def test_selected_channel_keeps_forecast_share_of_unselected_channel(self):
        cids=list(self.scenario.state['channels'])
        state,result=self.run_fill(channels=json.dumps([cids[0]]))
        self.assertEqual(D(state['groups'][self.gid]['channels'][cids[0]]['input']),33)
        self.assertEqual(state['groups'][self.gid]['channels'][cids[1]]['input'],'0')
        self.assertEqual(result['count'],1)
    def test_monthly_quantities_scale_and_annual_input_keeps_existing_fact(self):
        self.scenario.state['volume_mode']='annual'
        group=self.scenario.state['groups'][self.gid]
        for line in group['channels'].values():
            line.update(input='15',fact='5',allocation='quantity')
            line['month_quantities']={month:('10' if month.endswith('-01') else '0') for month in line['months']}
        state,result=self.run_fill()
        line=next(iter(state['groups'][self.gid]['channels'].values()))
        self.assertEqual(D(line['input']),38)
        self.assertEqual(sum(D(v) for v in line['month_quantities'].values()),33)
        self.assertEqual(line['fact'],'5')
    def test_sources_use_correct_periods_version_channels_and_actual_money(self):
        with patch('base.split_sales_prefill.query',return_value=[('G','Дилеры','IN',10,150,100,0)]) as query:
            values,meta=source(self.scenario,'history_forecast',2026,'')
            self.assertEqual(query.call_args.args[1][:2],[date(2026,1,1),date(2026,11,7)])
            self.assertIn('AmountExwAEUSD',query.call_args.args[0])
            source(self.scenario,'erp',2027,"Version '2027")
            self.assertIn("Version '2027",query.call_args.args[1]);self.assertNotIn("Version '2027",query.call_args.args[0])
            self.assertIn('[даичи_СуммаДДП]',query.call_args.args[0])
            self.assertEqual(query.call_args.args[1][:2],[date(2027,1,1),date(2027,12,31)])
        with patch('base.split_sales_prefill.query',return_value=[('v1',)]):self.assertEqual(versions(self.scenario,{'year':'2026'}),['v1'])
    def test_invalid_scope_and_coefficients_never_query_source(self):
        for patch_values in [{'groups':'["foreign"]'},{'channels':'[]'},{'markup_coefficient':'0'},{'quantity_coefficient':'NaN'}]:
            with patch('base.split_sales_prefill.source') as read:
                response=self.client.post(self.url,{'action':'prefill_sales_plan','revision':self.scenario.revision,**self.params,**patch_values})
                self.assertEqual(response.status_code,400);read.assert_not_called()


@skipUnless(os.environ.get('RUN_BROWSER_TESTS')=='1' and importlib.util.find_spec('playwright'),'Requires Chromium')
class SalesPrefillBrowserTests(StaticLiveServerTestCase):
    setUp=setup
    def test_history_prefill_saves_quantity_and_markup_in_one_click(self):
        from playwright.sync_api import sync_playwright,expect
        with patch('base.split_sales_prefill.source',return_value=(self.records,{'method':'history_forecast'})),sync_playwright() as driver:
            browser=driver.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'),headless=True)
            context=browser.new_context(viewport={'width':1700,'height':1100})
            context.add_cookies([{'name':'sessionid','value':self.client.cookies['sessionid'].value,'url':self.live_server_url}])
            page=context.new_page();errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
            try:
                page.goto(self.live_server_url+self.url+'?tab=plan')
                page.locator('[data-sales-prefill-open]').click()
                dialog=page.locator('[data-sales-prefill-dialog]');expect(dialog).to_be_visible()
                dialog.locator('[data-prefill-quantity]').fill('1.1');dialog.locator('[data-prefill-markup]').fill('1.1')
                with page.expect_navigation():dialog.locator('[data-prefill-save]').click()
                expect(page.locator('[name^="q_"]').first).to_have_value('33.000000')
                expect(page.locator('[name^="markup_"]').first).to_have_value('1.650000')
                self.assertEqual(errors,[])
            finally:context.close();browser.close()
