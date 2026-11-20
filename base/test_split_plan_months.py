from copy import deepcopy
from decimal import Decimal
import os,importlib.util
from unittest import skipUnless
from django.test import TestCase
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from .test_split_brand_pages import setup_brands
from .split_plan_months import report
from .split_engine import evaluate

class PlanMonthsTests(TestCase):
    setUp=setup_brands
    def test_quantities_cost_and_revenue_match_engine_and_all_brand_scope(self):
        before=deepcopy(self.scenario.state);result=report(self.scenario,[self.gid],[])
        for cid,line in self.scenario.state['groups'][self.gid]['channels'].items():
            values=result['groups'][self.gid]['bands'][cid]
            self.assertEqual(sum(v['qty'] for v in values.values()),Decimal(line['input']))
            for month,v in values.items():
                one=deepcopy(self.scenario);one.state['groups']={self.gid:one.state['groups'][self.gid]}
                rows=[r for r in evaluate(one)['shipments'] if r['channel_id']==cid and r['month']==month]
                self.assertEqual(v['qty'],sum((r['quantity'] for r in rows if r['kind']=='indoor'),Decimal(0)))
                self.assertEqual(v['cost'],sum((r['cost'] for r in rows),Decimal(0)))
                self.assertEqual(v['revenue'],sum((r['revenue'] for r in rows),Decimal(0)))
        self.assertEqual(sum(v['qty'] for v in result['page']['all'].values()),30)
        self.assertEqual(sum(v['qty'] for v in result['all']['all'].values()),60)
        self.assertEqual(self.scenario.state,before)
    def test_missing_prices_do_not_hide_quantity_and_missing_distribution_is_unknown(self):
        group=self.scenario.state['groups'][self.gid];cid=next(iter(group['channels']))
        group['ddp']=None
        values=report(self.scenario,[self.gid],[])['groups'][self.gid]['bands'][cid]
        self.assertEqual(sum(v['qty'] for v in values.values()),10)
        self.assertTrue(all(v['cost'] is None for v in values.values()))
        group['channels'][cid]['input']='0'
        values=report(self.scenario,[self.gid],[])['groups'][self.gid]['bands'][cid]
        self.assertTrue(all(v['qty']==0 for v in values.values()))
        group['channels'][cid]['months']={m:None for m in values}
        values=report(self.scenario,[self.gid],[])['groups'][self.gid]['bands'][cid]
        self.assertTrue(all(v['qty'] is None for v in values.values()))
    def test_quantity_mode_and_power_totals_are_exact(self):
        group=self.scenario.state['groups'][self.gid];cid=next(iter(group['channels']));line=group['channels'][cid]
        line.update(allocation='quantity',month_quantities={m:'10' if i==0 else '0' for i,m in enumerate(line['months'])})
        result=report(self.scenario,[self.gid],[])['groups'][self.gid]
        for m,v in result['bands'][cid].items():
            self.assertEqual(sum(p[cid][m]['qty'] for p in result['powers'].values()),v['qty'])

@skipUnless(os.environ.get('RUN_BROWSER_TESTS')=='1' and importlib.util.find_spec('playwright'),'Requires Chromium')
class PlanMonthsBrowserTests(StaticLiveServerTestCase):
    setUp=setup_brands
    def test_expand_channel_total_and_power_rows_and_hide_channel_without_losing_edits(self):
        from playwright.sync_api import sync_playwright,expect
        cid=next(iter(self.scenario.state['channels']))
        with sync_playwright() as driver:
            browser=driver.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'),headless=True)
            context=browser.new_context(viewport={'width':1800,'height':1100})
            context.add_cookies([{'name':'sessionid','value':self.client.cookies['sessionid'].value,'url':self.live_server_url}])
            page=context.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            try:
                page.goto(self.live_server_url+self.url+'?tab=plan&brand_page=alpha')
                group=page.locator('[data-plan-group="'+self.gid+'"]');values=group.locator('[data-plan-month-band="'+cid+'"]')
                expect(values.first).not_to_be_visible()
                page.locator('[data-plan-month-toggle="'+cid+'"]').click()
                expect(values.first).to_be_visible();self.assertEqual(values.count(),12)
                page.locator('[data-plan-month-toggle=all]').click()
                expect(group.locator('[data-plan-month-band=all]').first).to_be_visible()
                page.locator('[data-plan-group-toggle]').first.click()
                expect(page.locator('[data-plan-power-group]').first.locator('[data-plan-month-band=all]').first).to_be_visible()
                field=page.locator('[name="q_'+self.gid+'_'+cid+'"]');field.fill('37')
                picker=page.locator('[data-sales-channel-filter]');picker.locator('summary').click();picker.locator('[data-sales-channel-choice="'+cid+'"]').uncheck()
                expect(values.first).not_to_be_visible();expect(field).to_have_value('37')
                picker.locator('[data-sales-channel-choice="'+cid+'"]').check();expect(values.first).to_be_visible()
                picker.locator('summary').click();page.locator('[data-plan-month-toggle="'+cid+'"]').click();expect(values.first).not_to_be_visible()
                page.locator('#split-plan-inputs fieldset').evaluate('node=>node.disabled=true')
                page.locator('[data-plan-month-toggle=all]').click();expect(group.locator('[data-plan-month-band=all]').first).not_to_be_visible()
                page.locator('[data-plan-month-toggle=all]').press('Enter');expect(group.locator('[data-plan-month-band=all]').first).to_be_visible()
                self.assertEqual(errors,[])
                page.screenshot(path='/tmp/plan-months-expanded.png')
            finally:context.close();browser.close()
