from copy import deepcopy
from datetime import date
from decimal import Decimal as D
import json,os,importlib.util
from unittest import skipUnless
from unittest.mock import Mock,patch
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from . import test_split_forecast_history as history_fixtures
from .split_forecast_history import preview,apply
from .split_forecast_plan import plan_source,versions,PLAN_SQL,VERSIONS_SQL


def setup(test):
    history_fixtures.HistoricalForecastTests.setUp(test)
    test.plans={}
    for key,power in test.scenario.state['groups'][test.gid]['powers'].items():
        for model in power['models']:
            test.plans[(test.key,model['name'].casefold())]={'past':D(80 if key=='07' else 50),'future':D(20 if key=='07' else 30),'unknown':False,'future_seen':True}
    for entry in test.data.values():entry['qty']*=12
    test.post={'method':'plan_execution','targets':json.dumps([test.gid]),'plan_version':'План 2026 · учебная версия','elapsed_from':'2026-01-01'}


class ForecastPlanTests(TestCase):
    setUp=setup
    def proposal(self,**extra):
        with patch('base.split_forecast_plan.plan_source',return_value=self.plans),patch('base.split_forecast_plan.fact_source',return_value=(self.data,['Дилеры'])):
            return preview(self.scenario,{**self.post,**extra})
    def test_execution_over_100_percent_times_future_plan_and_saved_provenance(self):
        before=deepcopy(self.scenario.state);result=self.proposal();row=result['rows'][0]
        self.assertEqual(D(row['proposed']),60)
        self.assertEqual([D(p['sets']) for p in row['powers']],[24,36])
        self.assertIn('120%',row['explanation'])
        self.assertEqual(self.scenario.state,before)
        state,count=apply(self.scenario,{'tokens':json.dumps([row['token']]),'only_empty':'1'})
        saved=state['groups'][self.gid]['forecast_history_source']
        self.assertEqual(saved['plan_version'],self.post['plan_version'])
        self.assertEqual(D(saved['past_fact']),156);self.assertEqual(D(saved['past_plan']),130)
        self.assertEqual(D(saved['remaining_plan']),50);self.assertEqual(saved['target_from'],'2026-11-07');self.assertEqual(saved['target_through'],'2026-12-31')
        self.assertEqual(state['groups'][self.gid]['channels'],before['groups'][self.gid]['channels'])
    def test_zero_actual_is_zero_but_zero_past_plan_and_missing_or_unknown_data_are_not(self):
        for entry in self.data.values():entry['qty']=D(0)
        self.assertEqual(D(self.proposal()['rows'][0]['proposed']),0)
        for entry in self.plans.values():entry['past']=D(0)
        row=self.proposal()['rows'][0];self.assertNotIn('token',row);self.assertIn('процент выполнения не определён',row['error'])
        self.plans={};row=self.proposal()['rows'][0];self.assertNotIn('token',row)
    def test_direct_save_and_revision_and_only_empty_guards(self):
        with patch('base.split_forecast_plan.plan_source',return_value=self.plans),patch('base.split_forecast_plan.fact_source',return_value=(self.data,['Дилеры'])):
            response=self.client.post(self.url,{'action':'calculate_save_forecast_history','revision':self.scenario.revision,'only_empty':'1',**self.post})
            self.assertEqual(response.status_code,200,response.content)
            self.scenario.refresh_from_db();saved=deepcopy(self.scenario.state)
            response=self.client.post(self.url,{'action':'calculate_save_forecast_history','revision':0,**self.post})
            self.assertEqual(response.status_code,400)
            response=self.client.post(self.url,{'action':'calculate_save_forecast_history','revision':self.scenario.revision,'only_empty':'1',**self.post})
            self.assertEqual(response.json()['count'],0)
        self.scenario.refresh_from_db();self.assertEqual(self.scenario.state,saved)
    def test_sql_binds_version_identical_periods_and_channels_and_closes_resources(self):
        connection,cursor=Mock(),Mock();cursor.fetchall.return_value=[('G','IN',80,20,0,1)]
        with patch('base.split_forecast_plan.connect_database',return_value=(connection,cursor)):
            result=plan_source(self.scenario,"План '2026",date(2026,1,1))
        args=cursor.execute.call_args.args
        self.assertEqual(args[1:5],(date(2026,1,1),date(2026,11,6),date(2026,11,7),date(2026,12,31)))
        self.assertIn("План '2026",args);self.assertNotIn("План '2026",args[0])
        self.assertIn('IsWorkDay=1',args[0]);self.assertIn('DataWH.erp.[РегистрыНакопления.ПланыПродаж]',args[0])
        self.assertEqual(result[(self.key,'in')]['past'],80)
        cursor.close.assert_called_once();connection.close.assert_called_once()
        connection,cursor=Mock(),Mock();cursor.fetchall.return_value=[('V1',),('V2',)]
        with patch('base.split_forecast_plan.connect_database',return_value=(connection,cursor)):
            self.assertEqual(versions(self.scenario),['V1','V2'])
        self.assertIn('p.[Сценарий]',cursor.execute.call_args.args[0])
    def test_invalid_version_dates_and_foreign_targets_do_not_query(self):
        for extra in ({'plan_version':''},{'elapsed_from':'2027-01-01'},{'targets':'["foreign"]'}):
            with patch('base.split_forecast_plan.plan_source') as source:
                with self.assertRaises(ValidationError):preview(self.scenario,{**self.post,**extra})
                source.assert_not_called()

    def test_forecast_is_capped_per_power_by_stock_plus_preplan_receipts(self):
        group=self.scenario.state['groups'][self.gid]
        group['powers']['07'].update(stock_indoor='3',stock_outdoor='30',prior_indoor='2',prior_outdoor='0')
        before=deepcopy(self.scenario.state)
        row=self.proposal()['rows'][0]
        self.assertEqual(D(row['proposed']),41)
        self.assertEqual([D(p['sets']) for p in row['powers']],[5,36])
        self.assertIn('Ограничено остатками',row['explanation'])
        state,_=apply(self.scenario,{'tokens':json.dumps([row['token']]),'only_empty':'1'})
        self.assertEqual(state['groups'][self.gid]['forecast_history_source']['unconstrained_forecast'],'60.000000')
        self.assertTrue(state['groups'][self.gid]['forecast_history_source']['stock_cap_applied'])
        for key,power in state['groups'][self.gid]['powers'].items():
            for kind in ('indoor','outdoor'):
                self.assertGreaterEqual(D(power['stock_'+kind])+D(power['prior_'+kind])-D(power['forecast_before']),0)
        self.assertEqual(self.scenario.state,before)
    def test_missing_or_negative_inventory_is_not_replaced_by_zero(self):
        power=self.scenario.state['groups'][self.gid]['powers']['07']
        power['stock_indoor']=None
        self.assertNotIn('token',self.proposal()['rows'][0])
        power.update(stock_indoor='-3',prior_indoor='1')
        row=self.proposal()['rows'][0];self.assertNotIn('token',row);self.assertIn('отрицателен',row['error'])

    def test_changing_inventory_after_plan_forecast_cannot_save_a_negative_opening(self):
        from .split_stock_workspace import candidate
        from .split_workflow import persist
        row=self.proposal()['rows'][0]
        raw=json.dumps({self.gid:{'07:stock_indoor':'0','07:prior_indoor':'0'}})
        with self.assertRaises(ValidationError):candidate(self.scenario,raw,forecasts=json.dumps([row['token']]))
        state,_=apply(self.scenario,{'tokens':json.dumps([row['token']])})
        state['groups'][self.gid]['powers']['07']['stock_indoor']='0'
        with self.assertRaises(ValidationError):persist(self.scenario,self.actor,self.scenario.revision,state,'Invalid stock')
        raw=json.dumps({self.gid:{'07:stock_indoor':'0','forecast_group':'0'}})
        self.assertEqual(candidate(self.scenario,raw,forecasts=json.dumps([row['token']]))['groups'][self.gid]['forecast_before_total'],'0')

    def group_plan(self,past='130',future='50'):
        name=self.scenario.state['groups'][self.gid]['ref']['name'].casefold()
        return {(self.key,'pg_'+name):{'past':D(past),'future':D(future),'unknown':False,'past_seen':D(past)!=0,'future_seen':True}}

    def test_group_pg_plan_uses_saved_shares_only_when_detail_is_missing(self):
        self.plans=self.group_plan()
        group=self.scenario.state['groups'][self.gid]
        group['powers']['07']['share']='30';group['powers']['09']['share']='70'
        row=self.proposal()['rows'][0]
        self.assertIn('token',row,row.get('error'))
        self.assertEqual([D(p['sets']) for p in row['powers']],[18,42])
        self.assertIn('сохранённым долям',row['explanation'])
        state,_=apply(self.scenario,{'tokens':json.dumps([row['token']])})
        origin=state['groups'][self.gid]['forecast_history_source']
        self.assertEqual(origin['power_share_fallback'],['past','future'])
        self.assertEqual(origin['power_shares'],{'07':'30','09':'70'})
        group['powers']['07']['stock_indoor']='5'
        self.assertEqual([D(p['sets']) for p in self.proposal()['rows'][0]['powers']],[5,42])

    def test_existing_erp_detail_has_priority_over_group_pg_totals(self):
        self.plans.update(self.group_plan('999','999'))
        row=self.proposal()['rows'][0]
        self.assertEqual([D(p['sets']) for p in row['powers']],[24,36])
        state,_=apply(self.scenario,{'tokens':json.dumps([row['token']])})
        self.assertEqual(state['groups'][self.gid]['forecast_history_source']['power_share_fallback'],[])

    def test_fallback_is_independent_for_past_and_remaining_plan_and_requires_valid_shares(self):
        for entry in self.plans.values():entry.update(future=D(0),future_seen=False)
        self.plans.update(self.group_plan('0','50'))
        row=self.proposal()['rows'][0];self.assertEqual([D(p['sets']) for p in row['powers']],[30,30])
        state,_=apply(self.scenario,{'tokens':json.dumps([row['token']])})
        self.assertEqual(state['groups'][self.gid]['forecast_history_source']['power_share_fallback'],['future'])
        self.scenario.state['groups'][self.gid]['powers']['07']['share']=None
        row=self.proposal()['rows'][0];self.assertNotIn('token',row);self.assertIn('100%',row['error'])
        self.plans={};self.assertNotIn('token',self.proposal()['rows'][0])

    def test_unknown_sku_is_not_mistaken_for_a_group_total(self):
        self.plans[(self.key,'unknown sku')]={'past':D(1),'future':D(1),'unknown':False,'future_seen':True}
        row=self.proposal()['rows'][0];self.assertNotIn('token',row);self.assertIn('Нет сопоставления',row['error'])

    def test_legacy_null_forecast_source_does_not_block_save(self):
        from .split_workflow import persist
        state=deepcopy(self.scenario.state);state['groups'][self.gid]['forecast_history_source']=None
        persist(self.scenario,self.actor,self.scenario.revision,state,'Legacy source')
        self.scenario.refresh_from_db();self.assertIsNone(self.scenario.state['groups'][self.gid]['forecast_history_source'])


@skipUnless(os.environ.get('RUN_BROWSER_TESTS')=='1' and importlib.util.find_spec('playwright'),'Requires Chromium')
class ForecastPlanBrowserTests(StaticLiveServerTestCase):
    setUp=setup
    def test_choose_erp_version_preview_and_save_without_required_preview(self):
        group=self.scenario.state['groups'][self.gid]
        self.plans={(self.key,'pg_'+group['ref']['name'].casefold()):{'past':D(130),'future':D(50),'unknown':False,'past_seen':True,'future_seen':True}}
        from playwright.sync_api import sync_playwright,expect
        with patch('base.split_forecast_plan.versions',return_value=['План 2026 · учебная версия']),patch('base.split_forecast_plan.plan_source',return_value=self.plans),patch('base.split_forecast_plan.fact_source',return_value=(self.data,['Дилеры'])),sync_playwright() as driver:
            browser=driver.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'),headless=True)
            context=browser.new_context(viewport={'width':1700,'height':1100})
            context.add_cookies([{'name':'sessionid','value':self.client.cookies['sessionid'].value,'url':self.live_server_url}])
            page=context.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            try:
                page.goto(self.live_server_url+self.url+'?tab=stock')
                page.locator('[data-forecast-plan-open]').click()
                dialog=page.locator('#split-forecast-history')
                expect(dialog.locator('[data-forecast-history-method]')).to_have_value('plan_execution')
                expect(dialog.locator('[data-forecast-history-plan-version] option')).to_have_count(2)
                dialog.locator('[data-forecast-history-plan-version]').select_option(self.post['plan_version'])
                expect(dialog.locator('[data-forecast-history-year]')).not_to_be_visible()
                dialog.locator('[data-forecast-history-preview]').click()
                expect(dialog.locator('[data-forecast-history-results]')).to_contain_text('120%')
                expect(dialog.locator('[data-forecast-history-results]')).to_contain_text('сохранённым долям')
                expect(dialog.locator('[data-forecast-history-results]')).to_contain_text('60')
                page.screenshot(path='/tmp/forecast-plan-execution.png')
                with page.expect_navigation():dialog.locator('[data-forecast-history-save]').click()
                expect(page.locator('[data-stock-field=forecast_group]').first).to_have_value('60.000000')
                self.assertEqual(errors,[])
            finally:context.close();browser.close()
        self.scenario.refresh_from_db();self.assertEqual(self.scenario.state['groups'][self.gid]['forecast_history_source']['method'],'plan_execution')

    def test_direct_save_limits_forecast_to_available_stock_and_receipts(self):
        from playwright.sync_api import sync_playwright,expect
        for power in self.scenario.state['groups'][self.gid]['powers'].values():
            power.update(stock_indoor='2',prior_indoor='1',stock_outdoor='4',prior_outdoor='0')
        self.scenario.save(update_fields=['state'])
        with patch('base.split_forecast_plan.versions',return_value=[self.post['plan_version']]),patch('base.split_forecast_plan.plan_source',return_value=self.plans),patch('base.split_forecast_plan.fact_source',return_value=(self.data,['Дилеры'])),sync_playwright() as driver:
            browser=driver.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'),headless=True)
            context=browser.new_context(viewport={'width':1700,'height':1100})
            context.add_cookies([{'name':'sessionid','value':self.client.cookies['sessionid'].value,'url':self.live_server_url}])
            page=context.new_page()
            try:
                page.goto(self.live_server_url+self.url+'?tab=stock')
                page.locator('[data-forecast-plan-open]').click()
                version=page.locator('[data-forecast-history-plan-version]');expect(version.locator('option')).to_have_count(2)
                version.select_option(self.post['plan_version'])
                with page.expect_navigation():page.locator('[data-forecast-history-save]').click()
                expect(page.locator('[data-stock-field=forecast_group]').first).to_have_value('6')
            finally:context.close();browser.close()
        self.scenario.refresh_from_db()
        group=self.scenario.state['groups'][self.gid]
        self.assertTrue(group['forecast_history_source']['stock_cap_applied'])
        for power in group['powers'].values():
            self.assertGreaterEqual(D(power['stock_indoor'])+D(power['prior_indoor'])-D(power['forecast_before']),0)
