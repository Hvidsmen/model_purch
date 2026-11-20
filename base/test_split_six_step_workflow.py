import importlib.util
import json
import os
from copy import deepcopy
from unittest import skipUnless
from django.test import TestCase
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from .test_split_stock_workspace import StockWorkspaceTests


class SixStepWorkflowTests(TestCase):
    def setUp(self): StockWorkspaceTests.setUp(self)

    def fields(self):
        group = self.scenario.state['groups'][self.gid]
        fields = {'revision':str(self.scenario.revision),'plan_group_ids':json.dumps([self.gid]),'ddp_'+self.gid:group['ddp'],'buy_'+self.gid:'17'}
        for cid,line in group['channels'].items():
            fields.update({'q_'+self.gid+'_'+cid:'600','markup_'+self.gid+'_'+cid:line['markup'],'fact_'+self.gid+'_'+cid:'0'})
        return fields

    def test_navigation_has_seven_independent_routes_without_substeps(self):
        routes=[('composition',{}),('distribution',{'section':'months'}),('distribution',{'section':'powers'}),('stock',{'stock_step':'opening'}),('stock',{'stock_step':'targets'}),('plan',{}),('checks',{})]
        for index,(tab,query) in enumerate(routes,1):
            response=self.client.get(self.url,{'tab':tab,**query})
            self.assertEqual(response.status_code,200)
            self.assertEqual(response.context['workflow_ux']['current']['number'],index)
            self.assertEqual(len(response.context['workflow_ux']['stages']),7)
            self.assertNotContains(response,'class="split-stock-steps"')
            self.assertNotContains(response,'aria-label="Распределение"')
        self.assertContains(response,'name="action" value="approve"')
        self.assertIsNone(response.context['workflow_ux']['next'])
        response=self.client.get(self.url,{'tab':'plan'})
        self.assertContains(response,'name="buy_'+self.gid+'"')
        self.assertNotContains(response,'Проверки перед утверждением')
        self.assertNotContains(response,'name="action" value="approve"')
        self.assertEqual(response.context['workflow_ux']['next']['key'],'checks')
        response=self.client.get(self.url,{'tab':'stock','stock_step':'supplies'})
        self.assertEqual(response.context['tab'],'plan')

    def test_preview_current_sales_and_purchases_does_not_write(self):
        before=deepcopy(self.scenario.state)
        response=self.client.post(self.url,{'action':'preview_sales_balance',**self.fields()})
        self.assertEqual(response.status_code,200,response.content)
        row=response.json()['rows'][self.gid]
        self.assertEqual(float(row['planned']),1200)
        self.assertEqual(float(row['closing']),-683)
        self.assertEqual(float(row['target']),180)
        self.scenario.refresh_from_db();self.assertEqual(self.scenario.state,before)

    def test_calculation_uses_unsaved_plan_and_saves_atomically(self):
        response=self.client.post(self.url,{'action':'save_plan_rows','calculate_supplies':'1',**self.fields()})
        self.assertEqual(response.status_code,200,response.content)
        self.scenario.refresh_from_db()
        group=self.scenario.state['groups'][self.gid]
        self.assertEqual(float(group['buy']),880)
        self.assertEqual(sum(float(line['input']) for line in group['channels'].values()),1200)
        self.assertEqual(group['buy_source']['kind'],'calculated')

    def test_calculation_failure_keeps_sales_and_supplies_unchanged(self):
        self.group_state['target_stock_percent']=None;self.scenario.save(update_fields=['state'])
        before=deepcopy(self.scenario.state)
        response=self.client.post(self.url,{'action':'save_plan_rows','calculate_supplies':'1',**self.fields()})
        self.assertEqual(response.status_code,400)
        self.scenario.refresh_from_db();self.assertEqual(self.scenario.state,before)

    def test_manual_save_keeps_purchase_override(self):
        response=self.client.post(self.url,{'action':'save_plan_rows',**self.fields()})
        self.assertEqual(response.status_code,200,response.content)
        self.scenario.refresh_from_db();self.assertEqual(self.scenario.state['groups'][self.gid]['buy'],'17')


@skipUnless(os.environ.get('RUN_BROWSER_TESTS')=='1' and importlib.util.find_spec('playwright'),'Requires Chromium')
class SixStepWorkflowBrowserTests(StaticLiveServerTestCase):
    def setUp(self): StockWorkspaceTests.setUp(self)

    def test_sales_purchases_live_balance_and_atomic_save(self):
        from playwright.sync_api import sync_playwright,expect
        with sync_playwright() as driver:
            browser=driver.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'),headless=True)
            context=browser.new_context(viewport={'width':1700,'height':1000})
            context.add_cookies([{'name':'sessionid','value':self.client.cookies['sessionid'].value,'url':self.live_server_url}])
            page=context.new_page();errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
            page.goto(self.live_server_url+self.url+'?tab=plan')
            expect(page.locator('.split-workflow-step')).to_have_count(7)
            expect(page.locator('.split-workflow-step[aria-current]')).to_contain_text('Продажи и закупки')
            page.locator('.split-sales-view > summary').click()
            page.locator('[data-split-metric-choice="incoming"]').check()
            cid=next(iter(self.scenario.state['channels']))
            page.locator('[name="q_'+self.gid+'_'+cid+'"]').fill('700')
            page.get_by_label('Новые поставки G',exact=True).fill('100')
            expect(page.locator('[data-plan-balance-status]')).to_contain_text('Предварительный баланс')
            expect(page.locator('[data-plan-group] [data-plan-inventory="closing"]')).to_contain_text('-600')
            page.get_by_role('button',name='Рассчитать закупки и сохранить план',exact=True).click()
            page.wait_for_load_state('networkidle')
            expect(page.get_by_label('Новые поставки G',exact=True)).to_have_value('880.000000')
            self.assertEqual(errors,[])
            page.screenshot(path='/tmp/six-step-workflow.png',full_page=False)
            context.close();browser.close()
        self.scenario.refresh_from_db();self.assertEqual(float(self.scenario.state['groups'][self.gid]['buy']),880)

    def test_save_and_continue_keeps_target_policy_before_opening_sales(self):
        from playwright.sync_api import sync_playwright,expect
        with sync_playwright() as driver:
            browser=driver.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'),headless=True)
            context=browser.new_context(viewport={'width':1700,'height':1000})
            context.add_cookies([{'name':'sessionid','value':self.client.cookies['sessionid'].value,'url':self.live_server_url}])
            page=context.new_page()
            page.goto(self.live_server_url+self.url+'?tab=stock&stock_step=targets')
            page.get_by_label('Целевой процент G',exact=True).fill('20')
            page.locator('[data-split-save-next]').click()
            expect(page.locator('.split-workflow-step[aria-current]')).to_contain_text('Продажи и закупки')
            self.assertIn('tab=plan',page.url)
            context.close();browser.close()
        self.scenario.refresh_from_db();self.assertEqual(float(self.scenario.state['groups'][self.gid]['target_stock_percent']),20)
