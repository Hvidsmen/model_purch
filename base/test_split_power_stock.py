import importlib.util
import json
import os
from copy import deepcopy
from decimal import Decimal as D
from unittest import skipUnless
from unittest.mock import patch
from django.test import TestCase
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from . import test_split_power_grid as fixtures
from .split_power_stock import parameters
from .split_history import normalized


def setup(test):fixtures.PowerGridTests.setUp(test)


def source(test):
    group=test.scenario.state['groups'][test.gid]
    result={}
    for key,power in group['powers'].items():
        models={model['id']:model for model in power['models']}
        for kind,value in [('indoor',10 if key=='07' else 20),('outdoor',30 if key=='07' else 60)]:
            result[(group['ref']['source_key'],normalized(models[power[kind]]['name']))]={'qty':D(5),'cost':D(5*value),'unknown':False}
    return result,{'stock_date':str(test.scenario.stock_date),'warehouse_purpose':'Продажа'}


def current(test):
    return {key:{'factor':power.get('factor'),'cost':power.get('indoor_cost_percent'),'indoor':power.get('indoor'),'outdoor':power.get('outdoor')}
            for key,power in test.scenario.state['groups'][test.gid]['powers'].items() if power['active']}


class PowerStockTests(TestCase):
    setUp=setup

    def test_ddp_ratios_and_component_percent_use_selected_models_without_touching_shares(self):
        group=self.scenario.state['groups'][self.gid];before=deepcopy(group)
        values,issues=parameters(group,current(self),source(self)[0],False)
        self.assertFalse(issues)
        self.assertEqual(D(values['07']['factor']),1)
        self.assertEqual(D(values['09']['factor']),2)
        self.assertEqual(D(values['07']['cost']),25)
        self.assertEqual(D(values['09']['cost']),25)
        self.assertEqual(group,before)
        kept,_=parameters(group,current(self),source(self)[0],True)
        self.assertEqual(D(kept['07']['cost']),40)

    def test_missing_or_other_group_stock_does_not_fill_or_zero_costs(self):
        group=self.scenario.state['groups'][self.gid];values=current(self)
        for params in values.values():params.update(factor=None,cost=None)
        costs=source(self)[0]
        costs={('Other',model):value for (_,model),value in costs.items()}
        filled,issues=parameters(group,values,costs,False)
        self.assertTrue(issues)
        self.assertTrue(all(params['factor'] is None and params['cost'] is None for params in filled.values()))
        costs=source(self)[0]
        for entry in costs.values():entry['unknown']=True
        filled,issues=parameters(group,values,costs,False)
        self.assertTrue(issues);self.assertTrue(all(params['cost'] is None for params in filled.values()))

    def test_preview_and_save_signed_source_are_revision_guarded_and_keep_distribution(self):
        before=deepcopy(self.scenario.state)
        payload={'action':'preview_power_stock_parameters','revision':self.scenario.revision,'only_empty':'0',
                 'rows':json.dumps([{'gid':self.gid,'parameters':current(self)}])}
        with patch('base.split_power_stock.stock',return_value=source(self)) as load:
            response=self.client.post(self.url,payload)
            self.assertEqual(response.status_code,200,response.content);load.assert_called_once()
        self.scenario.refresh_from_db();self.assertEqual(self.scenario.state,before)
        proposal=response.json()['rows'][0]
        row={'gid':self.gid,'mode':'manual','unit':'percent','values':{'07':'50','09':'50'},'parameters':proposal['parameters'],'stock_token':proposal['token']}
        # Equivalent formatting of decimal fields is not a manual adjustment.
        row['parameters']['07']['factor']='1'
        save={'action':'save_power_grid','revision':self.scenario.revision,'save_draft':'1','grid_payload':json.dumps([row])}
        response=self.client.post(self.url,save);self.assertEqual(response.status_code,200,response.content)
        self.scenario.refresh_from_db();group=self.scenario.state['groups'][self.gid]
        self.assertEqual(group['powers']['09']['factor'],'2.000000')
        self.assertFalse(group['power_cost_source']['adjusted'])
        self.assertEqual([p['share'] for p in group['powers'].values()],['50','50'])
        self.assertEqual(self.client.post(self.url,payload).status_code,400)
        row['stock_token']='tampered';save['revision']=self.scenario.revision
        self.assertEqual(self.client.post(self.url,{**save,'grid_payload':json.dumps([row])}).status_code,400)

    def test_bulk_uses_one_stock_query_and_keeps_each_group_costs_separate(self):
        group=deepcopy(self.scenario.state['groups'][self.gid]);group['ref']['source_key']='OTHER'
        self.scenario.state['groups']['other']=group;self.scenario.save(update_fields=['state'])
        costs,origin=source(self)
        costs.update({('OTHER',model):{**entry,'cost':entry['cost']*2} for (_,model),entry in list(costs.items())})
        payload=[{'gid':gid,'parameters':current(self)} for gid in (self.gid,'other')]
        with patch('base.split_power_stock.stock',return_value=(costs,origin)) as load:
            response=self.client.post(self.url,{'action':'preview_power_stock_parameters','revision':self.scenario.revision,
                'only_empty':'0','rows':json.dumps(payload)})
            self.assertEqual(response.status_code,200,response.content);load.assert_called_once()
        self.assertEqual(len(response.json()['rows']),2)
        self.assertTrue(all(D(row['parameters']['09']['factor'])==2 for row in response.json()['rows']))

    def test_unauthorized_group_is_rejected_before_source_query(self):
        with patch('base.split_power_stock.stock') as load:
            response=self.client.post(self.url,{'action':'preview_power_stock_parameters','revision':self.scenario.revision,
                'rows':json.dumps([{'gid':'foreign','parameters':{}}])})
            self.assertEqual(response.status_code,400);load.assert_not_called()


@skipUnless(os.environ.get('RUN_BROWSER_TESTS')=='1' and importlib.util.find_spec('playwright'),'Requires Chromium')
class PowerStockBrowserTests(StaticLiveServerTestCase):
    setUp=setup

    def test_fill_cost_from_stock_keep_shares_and_save(self):
        from playwright.sync_api import sync_playwright,expect
        with patch('base.split_power_stock.stock',return_value=source(self)),sync_playwright() as driver:
            browser=driver.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'),headless=True)
            context=browser.new_context(viewport={'width':1700,'height':1100})
            context.add_cookies([{'name':'sessionid','value':self.client.cookies['sessionid'].value,'url':self.live_server_url}])
            page=context.new_page();errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
            try:
                page.goto(self.live_server_url+self.url+'?tab=distribution&section=powers')
                row=page.locator('[data-grid-row]');shares=row.locator('[data-month]').evaluate_all('(items)=>items.map(item=>item.value)')
                expect(page.locator('[data-power-stock-all]')).to_have_count(0)
                expect(page.locator('[data-power-stock-row]')).to_have_count(0)
                page.locator('.split-distribution-bulk > summary').click()
                expect(page.locator('[data-power-stock-fill]')).not_to_be_visible()
                page.locator('[data-grid-bulk-mode]').select_option('stock_ddp')
                expect(page.locator('[data-power-stock-fill]')).to_be_visible()
                expect(page.locator('[data-grid-share-empty]')).not_to_be_visible()
                page.locator('[data-power-stock-fill]').select_option('0')
                page.locator('[data-grid-bulk-apply]').click()
                expect(row.locator('[data-power-stock-source]')).to_contain_text('DDP из стока')
                self.assertEqual(row.locator('[data-month]').evaluate_all('(items)=>items.map(item=>item.value)'),shares)
                expect(row.locator('[data-power-config="09"] [data-power-param=factor]')).to_have_value('2.000000')
                expect(row.locator('[data-power-config="07"] [data-power-param=cost]')).to_have_value('25.000000')
                with page.expect_navigation():page.locator('[data-grid-save]').click()
                expect(page.locator('[data-power-stock-source]')).to_contain_text('DDP из стока')
                self.assertEqual(errors,[])
            finally:context.close();browser.close()
