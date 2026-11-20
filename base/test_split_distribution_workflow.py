from copy import deepcopy
from decimal import Decimal
import json,os,importlib.util
from unittest import skipUnless
from django.test import TestCase
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from .test_split_brand_pages import setup_brands
from .split_engine import evaluate

class DistributionWorkflowTests(TestCase):
    setUp=setup_brands
    def test_zero_plan_rows_are_visible_and_progress_includes_every_channel(self):
        for line in self.scenario.state['groups'][self.gid]['channels'].values():line['input']='0'
        self.scenario.save(update_fields=['state'])
        response=self.client.get(self.url,{'tab':'distribution','brand_page':'alpha'})
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.context['distribution_overview']['shown'],2)
        self.assertEqual(response.context['distribution_overview']['required_total'],2)
        self.assertContains(response,'data-month-grid')
        self.assertNotContains(response,'У этого бренда нет объёма для распределения')
        response=self.client.get(self.url,{'tab':'distribution','brand_page':'alpha','positive_only':'1'})
        self.assertEqual(response.context['distribution_overview']['shown'],0)
        response=self.client.get(self.url,{'tab':'distribution','brand_page':'alpha','section':'powers'})
        self.assertEqual(response.context['distribution_overview']['shown'],1)
        self.assertEqual(response.context['distribution_overview']['required_total'],1)

    def test_percentage_distribution_saves_before_sales_and_survives_volume_change(self):
        from .split_engine import planning_months
        from .split_month_grid import apply_grid
        from .split_distribution_overview import overview
        group=self.scenario.state['groups'][self.gid]
        for line in group['channels'].values():line['input']='0'
        months=planning_months(self.scenario)
        rows=[{'gid':self.gid,'cid':cid,'mode':'manual','unit':'percent','values':{m:'100' if i==0 else '0' for i,m in enumerate(months)}} for cid in group['channels']]
        state,count=apply_grid(self.scenario,json.dumps(rows))
        self.assertEqual(count,len(group['channels']))
        self.scenario.state=state
        report=overview(self.scenario,'months',{})
        own=[row for row in report['rows'] if row['gid']==self.gid]
        self.assertTrue(all(row['status']['key']=='ready' for row in own))
        self.assertTrue(all(row['allocated']==0 for row in own))
        for line in state['groups'][self.gid]['channels'].values():line['input']='80'
        own=[row for row in overview(self.scenario,'months',{})['rows'] if row['gid']==self.gid]
        self.assertTrue(all(row['allocated']==80 and row['remaining']==0 for row in own))
    def test_incomplete_manual_draft_saves_but_does_not_pass_validation(self):
        from .split_engine import planning_months
        cid=next(iter(self.scenario.state['channels']));months=planning_months(self.scenario)
        values={month:None for month in months};values[months[0]]='90'
        before=deepcopy(self.scenario.state['groups']['other'])
        row={'gid':self.gid,'cid':cid,'mode':'manual','unit':'percent','values':values}
        response=self.client.post(self.url,{'action':'save_month_grid','revision':self.scenario.revision,'save_draft':'1','grid_payload':json.dumps([row])})
        self.assertEqual(response.status_code,200,response.content)
        self.scenario.refresh_from_db();self.assertEqual(self.scenario.state['groups']['other'],before)
        self.assertTrue(evaluate(self.scenario)['errors'])
        self.assertIsNone(self.scenario.state['groups'][self.gid]['channels'][cid]['months'][months[1]])
        response=self.client.get(self.url,{'tab':'distribution','brand_page':'alpha'})
        first=next(row for row in response.context['distribution_overview']['rows'] if row['cid']==cid)
        self.assertEqual(first['allocated'],9);self.assertEqual(first['remaining'],1)
        self.assertEqual(self.client.post(self.url,{'action':'save_month_grid','revision':0,'save_draft':'1','grid_payload':json.dumps([row])}).status_code,400)
    def test_power_draft_can_store_partial_shares_and_missing_models(self):
        values={'07':'80','09':None}
        row={'gid':self.gid,'mode':'manual','unit':'percent','values':values,'parameters':{'07':{'factor':'1','cost':None,'indoor':None,'outdoor':None}}}
        response=self.client.post(self.url,{'action':'save_power_grid','revision':self.scenario.revision,'save_draft':'1','grid_payload':json.dumps([row])})
        self.assertEqual(response.status_code,200,response.content)
        self.scenario.refresh_from_db();self.assertTrue(evaluate(self.scenario)['errors'])
        self.assertIsNone(self.scenario.state['groups'][self.gid]['powers']['09']['share'])

@skipUnless(os.environ.get('RUN_BROWSER_TESTS')=='1' and importlib.util.find_spec('playwright'),'Requires Chromium')
class DistributionWorkflowBrowserTests(StaticLiveServerTestCase):
    setUp=setup_brands
    def test_explicit_fill_preserves_existing_then_partial_draft_is_saved(self):
        from playwright.sync_api import sync_playwright,expect
        with sync_playwright() as driver:
            browser=driver.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'),headless=True)
            context=browser.new_context(viewport={'width':1700,'height':1100})
            try:
                context.add_cookies([{'name':'sessionid','value':self.client.cookies['sessionid'].value,'url':self.live_server_url}])
                page=context.new_page();errors=[];requests=[]
                page.on('pageerror',lambda error:errors.append(str(error)))
                page.on('request',lambda req:requests.append(req.post_data or '') if req.method=='POST' else None)
                page.goto(self.live_server_url+self.url+'?tab=distribution&brand_page=alpha')
                expect(page.locator('[data-grid-row]')).to_have_count(2)
                self.assertFalse(any('preview_month_grid' in value for value in requests))
                page.locator('.split-distribution-bulk>summary').click()
                page.locator('[data-grid-bulk-mode]').select_option('equal')
                page.locator('[data-grid-bulk-apply]').click()
                expect(page.locator('[data-grid-bulk-result]')).to_contain_text('Нет подходящих строк')
                page.locator('[data-grid-global-unit]').select_option('quantity')
                values=page.locator('[data-grid-row]').first.locator('[data-month]').evaluate_all('(inputs)=>inputs.map(input=>input.value)')
                self.assertEqual(sum(Decimal(value.replace(',','.')) for value in values),10)
                page.locator('[data-grid-global-unit]').select_option('percent')
                row=page.locator('[data-grid-row]').first
                row.locator('[data-row-mode]').select_option('manual')
                row.locator('[data-month]').first.fill('90')
                expect(row.locator('[data-row-remaining]')).not_to_have_text('0')
                row.locator('[data-row-jump]').click()
                expect(row.locator('[data-month]').last).to_be_focused()
                with page.expect_navigation():page.get_by_role('button',name='Сохранить черновик',exact=True).click()
                expect(page.locator('[data-grid-row]').first.locator('[data-month]').first).to_have_value('90')
                page.screenshot(path='/tmp/split-distribution-workflow.png',full_page=True)
                self.assertEqual(errors,[])
            finally:context.close();browser.close()

    def test_zero_plan_rows_can_be_filled_and_saved_in_browser(self):
        from playwright.sync_api import sync_playwright,expect
        for group in self.scenario.state['groups'].values():
            for line in group['channels'].values():line['input']='0'
        self.scenario.save(update_fields=['state'])
        with sync_playwright() as driver:
            browser=driver.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'),headless=True)
            context=browser.new_context(viewport={'width':1700,'height':1100})
            context.add_cookies([{'name':'sessionid','value':self.client.cookies['sessionid'].value,'url':self.live_server_url}])
            page=context.new_page();errors=[]
            page.on('pageerror',lambda error:errors.append(str(error)))
            page.goto(self.live_server_url+self.url+'?tab=distribution&brand_page=alpha')
            expect(page.locator('[data-grid-row]')).to_have_count(2)
            page.locator('.split-distribution-bulk>summary').click()
            page.locator('[data-grid-only-empty]').uncheck()
            page.locator('[data-grid-bulk-mode]').select_option('equal')
            page.locator('[data-grid-bulk-apply]').click()
            expect(page.locator('[data-grid-bulk-result]')).to_contain_text('Обработано строк: 2')
            with page.expect_navigation():page.locator('[data-grid-save]').click()
            expect(page.locator('[data-grid-row]')).to_have_count(2)
            for row in page.locator('[data-grid-row]').all():
                values=row.locator('[data-month]').evaluate_all('(inputs)=>inputs.map(input=>input.value)')
                self.assertEqual(sum(Decimal(value.replace(',','.')) for value in values),100)
            self.assertEqual(errors,[])
            browser.close()
        self.scenario.refresh_from_db()
        self.assertTrue(all(line['distribution_mode']=='equal' and line['input']=='0' for line in self.scenario.state['groups'][self.gid]['channels'].values()))
