import importlib.util
import os
from pathlib import Path
from unittest import skipUnless
from unittest.mock import patch
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from . import test_split_inventory as inventory_fixtures


@skipUnless(os.environ.get('RUN_BROWSER_TESTS')=='1' and importlib.util.find_spec('playwright'),'Requires Chromium')
class SplitInventoryBrowserTests(StaticLiveServerTestCase):
    def setUp(self):
        super().setUp();inventory_fixtures.SplitInventoryImportTests.setUp(self)

    def test_operator_loads_physical_block_counts_into_stock_form(self):
        from playwright.sync_api import sync_playwright,expect
        with sync_playwright() as driver:
            browser=driver.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'),headless=True)
            context=browser.new_context(viewport={'width':1550,'height':1000})
            context.add_cookies([{'name':'sessionid','value':self.client.cookies['sessionid'].value,'url':self.live_server_url}])
            page=context.new_page()
            try:
                page.goto(self.live_server_url+self.url)
                expect(page.get_by_role('button',name='Загрузить остатки и приходы из DataWH')).to_be_visible()
                with patch('base.split_inventory.source_rows',return_value=(self.stock,self.incomes)):
                    page.get_by_role('button',name='Загрузить остатки и приходы из DataWH').click()
                    expect(page.locator('[name="stock_indoor_0"]')).to_have_value('15')
                    expect(page.locator('[name="stock_outdoor_0"]')).to_have_value('12')
                    expect(page.locator('[name="prior_indoor_0"]')).to_have_value('6')
                    expect(page.locator('[name="incoming_outdoor_0"]')).to_have_value('15')
                    expect(page.get_by_text('Источник: DataWH.erp.StreamGoods',exact=False)).to_be_visible()
                Path('/workspace/output').mkdir(exist_ok=True)
                page.screenshot(path='/workspace/output/split-physical-inventory.png',full_page=True)
            finally:context.close();browser.close()
