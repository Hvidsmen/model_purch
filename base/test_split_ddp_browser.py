import importlib.util,os
from unittest import skipUnless
from unittest.mock import patch
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from . import test_split_ddp as fixtures


@skipUnless(os.environ.get('RUN_BROWSER_TESTS')=='1' and importlib.util.find_spec('playwright'),'Requires Chromium')
class DdpBrowserTests(StaticLiveServerTestCase):
    def setUp(self):
        fixtures.DdpTests.setUp(self)
        self.scenario.state['groups'][self.gid]['ddp']=None
        self.scenario.save(update_fields=['state'])

    def test_fact_preview_cancel_insert_and_ctrl_s_preserve_other_edits(self):
        from playwright.sync_api import sync_playwright,expect
        with sync_playwright() as driver:
            browser=driver.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'),headless=True)
            context=browser.new_context(viewport={'width':1700,'height':1100})
            try:
                context.add_cookies([{'name':'sessionid','value':self.client.cookies['sessionid'].value,'url':self.live_server_url}])
                page=context.new_page();errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
                page.goto(self.live_server_url+self.url+'?tab=plan')
                qty=page.locator('#split-plan-inputs input[name^="q_"]').first;qty.fill('77')
                page.get_by_role('button',name='Заполнить DDP',exact=True).click()
                dialog=page.locator('#split-ddp-dialog')
                expect(dialog.locator('[data-ddp-count]')).to_have_text('Выбрано групп: 1')
                dialog.locator('[data-ddp-year]').fill('2025');dialog.locator('[data-ddp-surcharge]').fill('8')
                with patch('base.split_ddp.fact',return_value=(self.costs,self.metadata)):
                    dialog.get_by_role('button',name='Предпросмотр',exact=True).click()
                    expect(dialog.locator('[data-ddp-status]')).to_contain_text('Готово к вставке: 1')
                expect(page.get_by_label('DDP G',exact=True)).to_have_value('')
                expect(dialog.locator('[data-ddp-preview]')).to_contain_text('162.000000')
                dialog.get_by_role('button',name='Вставить в план').click()
                expect(page.get_by_label('DDP G',exact=True)).to_have_value('162.000000')
                expect(page.locator('.split-unsaved-dock')).to_be_visible()
                page.get_by_role('button',name='Отменить все изменения',exact=True).click()
                expect(page.get_by_label('DDP G',exact=True)).to_have_value('')
                expect(page.locator('input[name^="ddp_proposal_"]').first).to_have_value('')
                qty.fill('77')
                page.get_by_role('button',name='Заполнить DDP',exact=True).click()
                expect(dialog.locator('[data-ddp-count]')).to_have_text('Выбрано групп: 1')
                with patch('base.split_ddp.fact',return_value=(self.costs,self.metadata)):
                    dialog.get_by_role('button',name='Предпросмотр',exact=True).click()
                    expect(dialog.locator('[data-ddp-status]')).to_contain_text('Готово к вставке: 1')
                dialog.get_by_role('button',name='Вставить в план').click()
                with page.expect_navigation():page.keyboard.press('Control+s')
                expect(page.get_by_label('DDP G',exact=True)).to_have_value('162.000000')
                expect(page.locator('#split-plan-inputs input[name^="q_"]').first).to_have_value('77')
                self.assertEqual(errors,[])
                page.screenshot(path='/workspace/output/split-ddp-applied.png',full_page=True)
            finally:context.close();browser.close()
        self.scenario.refresh_from_db();group=self.scenario.state['groups'][self.gid]
        self.assertEqual(group['ddp_source']['year'],2025)
        self.assertEqual(group['powers']['09']['factor'],'2.000000')

    def test_purchase_auto_match_uses_final_cost_and_default_only_empty(self):
        purchase,good=fixtures.DdpTests.purchase(self)
        from playwright.sync_api import sync_playwright,expect
        with sync_playwright() as driver:
            browser=driver.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'),headless=True)
            context=browser.new_context(viewport={'width':1700,'height':1100})
            try:
                context.add_cookies([{'name':'sessionid','value':self.client.cookies['sessionid'].value,'url':self.live_server_url}])
                page=context.new_page();page.goto(self.live_server_url+self.url+'?tab=plan')
                page.get_by_label('DDP G',exact=True).fill('123')
                page.get_by_role('button',name='Заполнить DDP',exact=True).click()
                dialog=page.locator('#split-ddp-dialog')
                expect(dialog.locator('[data-ddp-count]')).to_have_text('Выбрано групп: 0')
                dialog.locator('[data-ddp-empty]').uncheck()
                dialog.locator('[data-ddp-source]').select_option('purchase')
                expect(dialog.locator('[data-ddp-status]')).to_contain_text('Выберите группы')
                dialog.locator('[data-ddp-purchase-id]').select_option(str(purchase.pk))
                expect(dialog.locator('[data-purchase-key="group"]')).to_have_value('G · #'+str(good.pk)+' · 325.0 USD')
                dialog.get_by_role('button',name='Выбрать все показанные',exact=True).click()
                dialog.get_by_role('button',name='Предпросмотр',exact=True).click()
                expect(dialog.locator('[data-ddp-status]')).to_contain_text('Готово к вставке: 1')
                expect(dialog.locator('[data-ddp-preview]')).to_contain_text('без детализации')
                dialog.get_by_role('button',name='Вставить в план').click()
                expect(page.get_by_label('DDP G',exact=True)).to_have_value('325.000000')
                with page.expect_navigation():page.keyboard.press('Control+s')
            finally:context.close();browser.close()
        self.scenario.refresh_from_db();self.assertEqual(self.scenario.state['groups'][self.gid]['ddp_source']['basis'],'group')
