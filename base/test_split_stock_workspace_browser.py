import importlib.util
import os
from decimal import Decimal
from unittest import skipUnless

from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from .test_split_brand_pages import setup_brands


@skipUnless(os.environ.get('RUN_BROWSER_TESTS')=='1' and importlib.util.find_spec('playwright'),'Requires Chromium')
class StockWorkspaceBrowserTests(StaticLiveServerTestCase):
    def setUp(self):
        setup_brands(self)
        for group in self.scenario.state['groups'].values():
            group['buy']='0'
            for line in group['channels'].values():line['input']='500'
            for power in group['powers'].values():
                power.update(stock_indoor='0',stock_outdoor='0',incoming_indoor='0',incoming_outdoor='0')
        self.scenario.save(update_fields=['state'])

    def test_enter_does_not_save_but_shortcut_and_save_button_do(self):
        from playwright.sync_api import sync_playwright,expect
        from concurrent.futures import ThreadPoolExecutor
        def saved_percent():
            scenario=type(self.scenario).objects.get(pk=self.scenario.pk)
            return next(g for g in scenario.state['groups'].values() if g['ref']['name']=='G').get('target_stock_percent')
        def read_percent():
            with ThreadPoolExecutor(max_workers=1) as executor:return executor.submit(saved_percent).result()
        with sync_playwright() as driver:
            browser=driver.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'),headless=True)
            page=browser.new_page()
            page.context.add_cookies([{'name':'sessionid','value':self.client.cookies['sessionid'].value,'url':self.live_server_url}])
            page.goto(self.live_server_url+self.url+'?tab=stock&stock_step=targets&brand_page=alpha')
            field=page.get_by_role('spinbutton',name='Целевой процент G',exact=True)
            field.fill('17')
            with page.expect_response(lambda r:r.request.method=='POST' and 'preview_stock_workspace' in (r.request.post_data or '')):
                field.press('Enter')
            expect(page.locator('[data-stock-save-status]')).to_contain_text('Изменено')
            self.assertNotEqual(read_percent(),'17')
            field.press('Control+s')
            expect(page.locator('[data-stock-save-status]')).to_have_text('Изменения сохранены')
            self.assertEqual(Decimal(read_percent()),17)
            field.fill('18')
            page.locator('[data-stock-save]').focus()
            page.locator('[data-stock-save]').press('Enter')
            expect(page.locator('[data-stock-save-status]')).to_have_text('Изменения сохранены')
            self.assertEqual(Decimal(read_percent()),18)
            browser.close()

    def test_opening_targets_proposal_and_single_save_with_brand_guard(self):
        from playwright.sync_api import sync_playwright,expect
        with sync_playwright() as driver:
            browser=driver.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'),headless=True)
            page=browser.new_page(viewport={'width':1440,'height':1050})
            errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
            page.context.add_cookies([{'name':'sessionid','value':self.client.cookies['sessionid'].value,'url':self.live_server_url}])
            base=self.live_server_url+self.url
            page.goto(base+'?tab=stock&stock_step=opening&brand_page=alpha')
            expect(page.locator('[data-stock-group]')).to_have_count(1)
            expect(page.locator('[data-stock-summary]')).to_have_count(2)
            expect(page.locator('[data-stock-group] [data-stock-characteristic]').first).not_to_be_visible()
            page.locator('[data-stock-toggle]').click()
            stock=page.get_by_role('spinbutton',name='Остаток внутренние G 07',exact=True)
            stock.fill('10')
            expect(page.locator('[data-stock-save-status]')).to_contain_text('Изменено 1 групп')
            page.get_by_role('link',name='Целевой запас',exact=False).first.click()
            expect(page.get_by_role('dialog',name='Несохранённые изменения')).to_be_visible()
            page.get_by_role('button',name='Сохранить и перейти',exact=True).click()
            expect(page.locator('[data-stock-workspace]')).to_have_attribute('data-step','targets')
            page.get_by_role('spinbutton',name='Целевой процент G',exact=True).fill('15')
            page.locator('[data-stock-save]').click()
            expect(page.locator('[data-stock-save-status]')).to_have_text('Изменения сохранены')
            expect(page.get_by_role('spinbutton',name='Целевой процент G',exact=True)).to_have_value('15')
            page.screenshot(path='/tmp/stock-workspace-targets.png',full_page=True)
            page.get_by_role('link',name='2. Спланировать поставки',exact=False).click()
            expect(page.locator('[data-stock-workspace]')).to_have_attribute('data-step','supplies')
            page.locator('[data-stock-proposal="supplies"]').click()
            page.locator('[data-stock-proposal-preview]').click()
            expect(page.locator('[data-stock-proposal-results]')).to_contain_text('1 150')
            page.locator('[data-stock-proposal-confirm]').click()
            expect(page.get_by_role('spinbutton',name='Новые поставки G',exact=True)).to_have_value('1150.000000')
            page.keyboard.press('Control+s')
            expect(page.locator('[data-stock-save-status]')).to_have_text('Изменения сохранены')
            expect(page.locator('[data-stock-group] [data-stock-result="closing"]')).to_have_text('150')
            page.locator('[data-stock-search]').fill('not found')
            expect(page.locator('[data-stock-group]')).not_to_be_visible()
            page.locator('[data-stock-search]').fill('')
            expect(page.locator('[data-stock-group]')).to_be_visible()
            page.locator('.split-stock-controls details').last.locator('summary').click()
            page.locator('[data-stock-characteristics]').check()
            expect(page.locator('[data-stock-group] [data-stock-characteristic]').first).to_be_visible()
            page.locator('[data-stock-nested]').check()
            expect(page.locator('.split-stock-nested-heading')).to_be_visible()
            page.locator('[data-stock-nested]').uncheck()
            page.locator('[data-stock-characteristics]').uncheck()
            page.locator('.split-stock-controls details').last.locator('summary').click()
            page.evaluate('window.scrollTo(0,0)')
            page.screenshot(path='/tmp/stock-workspace-supplies.png',full_page=True)
            page.set_viewport_size({'width':390,'height':844})
            self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'),390)
            self.assertEqual(errors,[])
            browser.close()
        self.scenario.refresh_from_db()
        own=self.scenario.state['groups'][self.gid]
        self.assertEqual(own['powers']['07']['stock_indoor'],'10')
        self.assertEqual(Decimal(own['target_stock_percent']),15)
        self.assertEqual(Decimal(own['buy']),1150)
        self.assertEqual(self.scenario.state['groups']['other']['buy'],'0')

    def test_history_is_confirmed_into_table_before_common_save(self):
        from datetime import date
        from unittest.mock import patch
        from playwright.sync_api import sync_playwright,expect
        self.scenario.stock_date=date(2026,11,6)
        for group in self.scenario.state['groups'].values():
            group['forecast_mode']='group';group['forecast_before_total']=None
        self.scenario.save(update_fields=['stock_date','state'])
        group=self.scenario.state['groups'][self.gid]
        facts={(group['ref']['source_key'],model['name'].casefold()):{'qty':Decimal(2),'unknown':False} for power in group['powers'].values() for model in power['models']}
        with patch('base.split_forecast_history.source',return_value=(facts,['Дилеры'])),sync_playwright() as driver:
            browser=driver.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'),headless=True)
            page=browser.new_page(viewport={'width':1440,'height':1050})
            page.context.add_cookies([{'name':'sessionid','value':self.client.cookies['sessionid'].value,'url':self.live_server_url}])
            requests=[];errors=[]
            page.on('pageerror',lambda error:errors.append(str(error)))
            page.on('request',lambda req:requests.append(req.post_data or '') if req.method=='POST' else None)
            page.goto(self.live_server_url+self.url+'?tab=stock&stock_step=opening&brand_page=alpha')
            page.locator('[data-forecast-history-open]').click()
            page.locator('[data-forecast-history-year]').fill('2025')
            page.locator('[data-forecast-history-preview]').click()
            expect(page.locator('[data-forecast-history-results]')).to_contain_text('G')
            page.locator('[data-forecast-history-apply]').click()
            expect(page.locator('#split-forecast-history')).not_to_be_visible()
            expect(page.get_by_role('spinbutton',name='Прогноз группы G',exact=True)).to_have_value('4.000000')
            expect(page.locator('[data-stock-save-status]')).to_contain_text('Изменено 1 групп')
            self.assertFalse(any('action=apply_forecast_history' in body for body in requests))
            page.keyboard.press('Control+s')
            expect(page.locator('[data-stock-save-status]')).to_have_text('Изменения сохранены')
            expect(page.get_by_role('spinbutton',name='Прогноз группы G',exact=True)).to_have_value('4.000000')
            self.assertEqual(errors,[])
            browser.close()
        self.scenario.refresh_from_db()
        self.assertEqual(self.scenario.state['groups'][self.gid]['forecast_mode'],'powers')
        self.assertIsNone(self.scenario.state['groups']['other']['forecast_before_total'])

    def test_automatic_percent_and_supplies_save_without_preview(self):
        from playwright.sync_api import sync_playwright,expect
        with sync_playwright() as driver:
            browser=driver.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'),headless=True)
            context=browser.new_context(viewport={'width':1700,'height':1100})
            context.add_cookies([{'name':'sessionid','value':self.client.cookies['sessionid'].value,'url':self.live_server_url}])
            page=context.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            try:
                page.goto(self.live_server_url+self.url+'?tab=stock&stock_step=targets&brand_page=alpha')
                page.locator('[data-stock-bulk-percent]').fill('15')
                with page.expect_navigation():page.locator('[data-stock-save-percent]').click()
                expect(page.locator('[data-stock-field=target_stock_percent]').first).to_have_value('15')
                page.get_by_role('link',name='2. Спланировать поставки',exact=False).click()
                page.locator('[data-stock-proposal=supplies]').click()
                with page.expect_navigation():page.locator('[data-stock-proposal-save]').click()
                expect(page.get_by_role('spinbutton',name='Новые поставки G',exact=True)).to_have_value('1150.000000')
                expect(page.locator('[data-stock-save-status]')).to_have_text('Изменения сохранены')
                self.assertEqual(errors,[])
            finally:context.close();browser.close()
        self.scenario.refresh_from_db();self.assertEqual(Decimal(self.scenario.state['groups'][self.gid]['buy']),1150)

    def test_power_detail_shows_kit_counts_and_updates_them_when_blocks_change(self):
        from datetime import date
        self.scenario.stock_date=date(2026,11,6)
        self.scenario.save(update_fields=['stock_date'])
        from playwright.sync_api import sync_playwright,expect
        with sync_playwright() as driver:
            browser=driver.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'),headless=True)
            context=browser.new_context(viewport={'width':1440,'height':1050})
            context.add_cookies([{'name':'sessionid','value':self.client.cookies['sessionid'].value,'url':self.live_server_url}])
            page=context.new_page()
            try:
                page.goto(self.live_server_url+self.url+'?tab=stock&stock_step=opening&brand_page=alpha')
                page.locator('[data-stock-toggle]').first.click()
                power=page.locator('[data-stock-child] [data-stock-power="07"]').first
                expect(power.locator('[data-stock-power-result="stock"]')).to_have_text('0')
                power.locator('[data-stock-field="07:stock_indoor"]').fill('315')
                power.locator('[data-stock-field="07:stock_outdoor"]').fill('42')
                expect(power.locator('[data-stock-power-result="stock"]')).to_have_text('42')
                power.locator('[data-stock-field="07:prior_indoor"]').fill('3')
                power.locator('[data-stock-field="07:prior_outdoor"]').fill('2')
                expect(power.locator('[data-stock-power-result="prior"]')).to_have_text('2')
            finally:context.close();browser.close()
