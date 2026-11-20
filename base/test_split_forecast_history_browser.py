import importlib.util,os
from decimal import Decimal
from unittest import skipUnless
from unittest.mock import patch
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from . import test_split_forecast_history as fixtures
from .split_preplan_forecast import resolve

@skipUnless(os.environ.get('RUN_BROWSER_TESTS')=='1' and importlib.util.find_spec('playwright'),'Requires Chromium')
class ForecastHistoryBrowserTests(StaticLiveServerTestCase):
    def setUp(self):fixtures.HistoricalForecastTests.setUp(self)

    def test_preview_and_apply_show_progress_and_persist_power_forecast(self):
        from playwright.sync_api import sync_playwright,expect
        with sync_playwright() as driver:
            browser=driver.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'),headless=True)
            context=browser.new_context(viewport={'width':1600,'height':1100})
            context.add_cookies([{'name':'sessionid','value':self.client.cookies['sessionid'].value,'url':self.live_server_url}])
            page=context.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            try:
                page.goto(self.live_server_url+self.url+'?tab=stock')
                page.get_by_role('button',name='Рассчитать прогноз по истории',exact=True).click()
                dialog=page.locator('#split-forecast-history')
                expect(dialog).to_be_visible();expect(dialog.locator('[data-forecast-history-year]')).to_have_value('2026');dialog.locator('[data-forecast-history-year]').fill('2025')
                dialog.locator('[data-forecast-history-adjustment]').fill('10')
                with patch('base.split_forecast_history.source',return_value=(self.data,['Дилеры'])):
                    dialog.locator('[data-forecast-history-preview]').click()
                    expect(dialog.locator('[data-forecast-history-results]')).to_contain_text('14,3')
                    expect(dialog.locator('[data-forecast-history-status]')).to_contain_text('2025-11-07')
                expect(dialog.locator('[data-forecast-history-progress]')).to_be_hidden()
                page.screenshot(path='/tmp/split-history-forecast-preview.png',full_page=True)
                dialog.locator('[data-forecast-history-apply]').click()
                expect(dialog).not_to_be_visible()
                expect(page.locator('[data-stock-save-status]')).to_contain_text('Изменено')
                with page.expect_navigation():page.locator('[data-stock-save]').click()
                expect(page.locator('[data-stock-field="forecast_group"]').first).to_have_value('14.300000')
                expect(page.locator('[data-stock-row-status]').first).to_be_visible()
                self.assertEqual(errors,[])
            finally:context.close();browser.close()
        self.scenario.refresh_from_db()
        self.assertEqual(resolve(self.scenario,self.scenario.state['groups'][self.gid])['total'],Decimal('14.3'))

    def test_calculate_and_save_skips_preview_and_common_save(self):
        from playwright.sync_api import sync_playwright,expect
        with patch('base.split_forecast_history.source',return_value=(self.data,['Дилеры'])),sync_playwright() as driver:
            browser=driver.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'),headless=True)
            context=browser.new_context(viewport={'width':1600,'height':1100})
            context.add_cookies([{'name':'sessionid','value':self.client.cookies['sessionid'].value,'url':self.live_server_url}])
            page=context.new_page()
            try:
                page.goto(self.live_server_url+self.url+'?tab=stock')
                page.get_by_role('button',name='Рассчитать прогноз по истории',exact=True).click()
                with page.expect_navigation():page.locator('[data-forecast-history-save]').click()
                expect(page.locator('[data-stock-field="forecast_group"]').first).to_have_value('2.306452')
                expect(page.locator('[data-stock-save-status]')).to_have_text('Изменения сохранены')
            finally:context.close();browser.close()
        self.scenario.refresh_from_db()
        self.assertEqual(resolve(self.scenario,self.scenario.state['groups'][self.gid])['total'],Decimal('2.306452'))
