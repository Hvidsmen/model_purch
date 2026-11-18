import importlib.util
import os
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
            page=context.new_page()
            try:
                page.goto(self.live_server_url+self.url)
                expect(page.get_by_role('button',name='Загрузить остатки и приходы всех групп')).to_be_visible()
                expect(page.locator('#split-stock-table [data-stock-group]')).to_have_count(2)
                expect(page.locator('#split-stock-table [name="stock_indoor_0"]').first).not_to_be_visible()
                page.get_by_role('button',name='Раскрыть группу G',exact=True).click()
                expect(page.locator('#split-stock-table [name="stock_indoor_0"]').first).to_be_visible()
                with patch('base.split_inventory.source_rows_many',return_value={self.gid:(self.stock,self.incomes),'other':([],[])}):
                    page.get_by_role('button',name='Загрузить остатки и приходы всех групп').click()
                    expect(page.locator('[name="stock_indoor_0"][form="split-stock-form-'+self.gid+'"]')).to_have_value('15')
                    expect(page.locator('[name="stock_outdoor_0"][form="split-stock-form-'+self.gid+'"]')).to_have_value('12')
                    expect(page.locator('[name="prior_indoor_0"][form="split-stock-form-'+self.gid+'"]')).to_have_value('6')
                    expect(page.locator('[name="incoming_outdoor_0"][form="split-stock-form-'+self.gid+'"]')).to_have_value('15')
                    expect(page.get_by_text('Источник: DataWH.erp.StreamGoods',exact=False).first).to_be_visible()
                expect(page.locator('.split-workspace table')).to_have_count(1)
                page.locator('[name="forecast_2026-11"][form="split-stock-form-'+self.gid+'"]').fill('2')
                page.locator('[name="forecast_2026-12"][form="split-stock-form-'+self.gid+'"]').fill('3')
                page.locator('.split-stock-tools-row[data-stock-child="'+self.gid+'"]').get_by_role('button',name='Сохранить группу').click()
                opening=page.locator('.split-stock-power-row[data-stock-child="'+self.gid+'"]').first
                expect(opening.locator('[data-opening="indoor"]')).to_have_text('16')
                expect(opening.locator('[data-opening="outdoor"]')).to_have_text('7')
                expect(opening.locator('[data-opening="sets"]')).to_have_text('7')
                # Saving G must not send the identically named inputs of H.
                page.get_by_role('button',name='Раскрыть группу H',exact=True).click()
                other=page.locator('.split-stock-power-row[data-stock-child="other"]')
                expect(other.locator('[name="stock_indoor_0"]')).to_have_value('0')
                expect(other.locator('[name="stock_outdoor_0"]')).to_have_value('0')
                page.get_by_role('button',name='Раскрыть группу H',exact=True).click()
                expect(other.locator('[name="stock_indoor_0"]')).not_to_be_visible()
                Path('/workspace/output').mkdir(exist_ok=True)
                page.screenshot(path='/workspace/output/split-physical-inventory.png',full_page=True)
            finally:context.close();browser.close()
