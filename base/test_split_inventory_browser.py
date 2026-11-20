import importlib.util
import os
import re
from pathlib import Path
from copy import deepcopy
from unittest import skipUnless
from unittest.mock import patch
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from . import test_split_inventory as inventory_fixtures


@skipUnless(os.environ.get('RUN_BROWSER_TESTS')=='1' and importlib.util.find_spec('playwright'),'Requires Chromium')
class SplitInventoryBrowserTests(StaticLiveServerTestCase):
    def setUp(self):
        super().setUp();inventory_fixtures.SplitInventoryImportTests.setUp(self)
        group=deepcopy(self.scenario.state['groups'][self.gid])
        group['ref'].update(id='other',name='H',source_key='H')
        self.scenario.state['groups']['other']=group
        self.scenario.save(update_fields=['state'])

    def test_operator_loads_physical_block_counts_into_stock_form(self):
        from playwright.sync_api import sync_playwright,expect
        with sync_playwright() as driver:
            browser=driver.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'),headless=True)
            context=browser.new_context(viewport={'width':1550,'height':1000})
            context.add_cookies([{'name':'sessionid','value':self.client.cookies['sessionid'].value,'url':self.live_server_url}])
            page=context.new_page();errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
            try:
                page.goto(self.live_server_url+self.url)
                expect(page.locator('[data-stock-group]')).to_have_count(2)
                page.locator('[data-stock-toggle="'+self.gid+'"]').click()
                page.locator('[data-stock-load-scope]').select_option('all')
                page.on('dialog',lambda dialog:dialog.accept())
                with patch('base.split_inventory.source_rows_many',return_value={self.gid:(self.stock,self.incomes),'other':([],[])}):
                    with page.expect_navigation():page.locator('[data-stock-load]').click()
                expect(page.get_by_label('Остаток внутренние G 07',exact=True)).to_have_value('15')
                expect(page.get_by_label('Остаток наружные G 07',exact=True)).to_have_value('12')
                expect(page.get_by_label('До начала внутренние G 07',exact=True)).to_have_value('6')
                expect(page.get_by_text('Источник остатков: DataWH.erp.StreamGoods',exact=False).first).to_be_visible()
                child=page.locator('[data-stock-child="'+self.gid+'"]')
                child.locator('details summary').click()
                page.get_by_label('Прогноз G 2026-11',exact=True).fill('2')
                page.get_by_label('Прогноз G 2026-12',exact=True).fill('3')
                with page.expect_navigation():page.locator('[data-stock-save]').click()
                expect(child.locator('[data-stock-power-result="opening"]')).to_have_text('7')
                page.get_by_label('Прогноз группы G',exact=True).fill('8')
                with page.expect_navigation():page.locator('[data-stock-save]').click()
                expect(page.get_by_label('Прогноз мощности G 07',exact=True)).to_have_value(re.compile(r'^8(?:\.0+)?$'))
                expect(child.locator('[data-stock-power-result="opening"]')).to_have_text('4')
                page.get_by_label('Прогноз мощности G 07',exact=True).fill('3')
                expect(page.get_by_label('Прогноз группы G',exact=True)).to_have_value('3')
                with page.expect_navigation():page.locator('[data-stock-save]').click()
                expect(child.locator('[data-stock-power-result="opening"]')).to_have_text('9')
                page.get_by_label('Прогноз группы H',exact=True).fill('0')
                with page.expect_navigation():page.locator('[data-stock-save]').click()
                page.locator('[data-stock-toggle="other"]').click()
                expect(page.get_by_label('Остаток внутренние H 07',exact=True)).to_have_value('0')
                expect(page.get_by_label('Остаток наружные H 07',exact=True)).to_have_value('0')
                self.assertEqual(errors,[])
                Path('/workspace/output').mkdir(exist_ok=True)
                page.screenshot(path='/workspace/output/split-physical-inventory.png',full_page=True)
            finally:context.close();browser.close()
