import importlib.util,os
from copy import deepcopy
from unittest import skipUnless
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from . import test_split_ddp as fixtures

@skipUnless(os.environ.get('RUN_BROWSER_TESTS')=='1' and importlib.util.find_spec('playwright'),'Requires Chromium')
class MetricPreferencesBrowserTests(StaticLiveServerTestCase):
    def setUp(self):fixtures.DdpTests.setUp(self)

    def test_preferences_hide_without_losing_values_and_persist_across_tabs(self):
        from playwright.sync_api import sync_playwright,expect
        before=deepcopy(self.scenario.state)
        with sync_playwright() as driver:
            browser=driver.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'),headless=True)
            context=browser.new_context(viewport={'width':1700,'height':1000})
            context.add_cookies([{'name':'sessionid','value':self.client.cookies['sessionid'].value,'url':self.live_server_url}])
            page=context.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            try:
                page.goto(self.live_server_url+self.url+'?tab=plan')
                page.locator('[data-split-metric-preferences] summary').click()
                ddp=page.get_by_label('DDP G',exact=True);value=ddp.input_value()
                original=page.locator('#split-plan-inputs').evaluate('(f)=>Array.from(new FormData(f).entries())')
                for metric in ['quantity','revenue','markup','ddp','stock','incoming','planned_incoming']:
                    page.locator('[data-split-metric-choice="'+metric+'"]').uncheck()
                expect(ddp).not_to_be_visible()
                page.get_by_role('button',name='Таблица',exact=True).click()
                page.get_by_role('button',name='Вложенный вид',exact=True).click()
                page.get_by_role('button',name='Раскрыть мощности G',exact=True).click()
                expect(page.locator('[data-plan-power-group] [data-split-metric="ddp"]').first).not_to_be_visible()
                expect(page.locator('[data-plan-inventory="opening"]').first).not_to_be_visible()
                self.assertEqual(ddp.input_value(),value)
                self.assertEqual(page.locator('#split-plan-inputs').evaluate('(f)=>Array.from(new FormData(f).entries())'),original)
                expect(page.locator('.split-unsaved-dock')).not_to_be_visible()
                page.reload()
                expect(page.locator('[data-split-metric-choice="ddp"]')).not_to_be_checked()
                expect(page.get_by_label('DDP G',exact=True)).not_to_be_visible()
                page.goto(self.live_server_url+self.url+'?tab=stock')
                expect(page.locator('[data-stock-band-heading="stock"]')).not_to_be_visible()
                expect(page.locator('[data-stock-band-heading="prior"]')).not_to_be_visible()
                expect(page.locator('[data-stock-band-heading="incoming"]')).not_to_be_visible()
                page.locator('[data-split-metric-preferences] summary').click()
                page.locator('[data-split-metrics-reset]').click()
                expect(page.locator('[data-stock-band-heading="stock"]')).to_be_visible()
                expect(page.locator('[data-stock-band-heading="incoming"]')).to_be_visible()
                page.screenshot(path='/tmp/split-metrics-stock.png',full_page=True)
                self.assertEqual(errors,[])
            finally:context.close();browser.close()
        self.scenario.refresh_from_db();self.assertEqual(self.scenario.state,before)
