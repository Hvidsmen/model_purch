import importlib.util,os
from unittest import skipUnless
from copy import deepcopy
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from .test_split_brand_pages import setup_brands

@skipUnless(os.environ.get('RUN_BROWSER_TESTS')=='1' and importlib.util.find_spec('playwright'),'Requires Chromium')
class BrandPagesBrowserTests(StaticLiveServerTestCase):
    setUp=setup_brands

    def test_brand_navigation_saves_visible_brand_and_keeps_tab_selection(self):
        from playwright.sync_api import sync_playwright,expect
        other=deepcopy(self.scenario.state['groups']['other'])
        with sync_playwright() as driver:
            browser=driver.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'),headless=True)
            context=browser.new_context(viewport={'width':1700,'height':1000})
            context.add_cookies([{'name':'sessionid','value':self.client.cookies['sessionid'].value,'url':self.live_server_url}])
            page=context.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            try:
                page.goto(self.live_server_url+self.url+'?tab=plan')
                expect(page.locator('[data-plan-group]')).to_have_count(1)
                page.get_by_label('DDP G',exact=True).fill('123')
                picker=page.locator('[data-brand-multiselect]');picker.locator('summary').click()
                picker.locator('[data-brand-choice=alpha]').uncheck()
                picker.locator('[data-brand-choice=beta]').check()
                picker.locator('[data-brands-apply]').click()
                expect(page.locator('.split-unsaved-dialog')).to_be_visible()
                with page.expect_navigation():page.get_by_role('button',name='Сохранить и перейти',exact=True).click()
                expect(page.locator('[data-brand-pages]')).to_have_attribute('data-brand-pages','beta')
                expect(page.locator('[data-plan-group]')).to_have_count(1)
                expect(page.get_by_label('DDP Beta group',exact=True)).to_be_visible()
                stock=page.get_by_role('link',name='Сток и закупки',exact=False).first
                self.assertIn('brand_page=beta',stock.get_attribute('href'))
                with page.expect_navigation():stock.click()
                expect(page.locator('[data-stock-group]')).to_have_count(1)
                expect(page.locator('[data-stock-group]')).to_have_attribute('data-stock-group','other')
                self.assertEqual(errors,[])
            finally:context.close();browser.close()
        self.scenario.refresh_from_db();self.assertEqual(self.scenario.state['groups']['other'],other)
        self.assertEqual(self.scenario.state['groups'][self.gid]['ddp'],'123')

    def test_multiple_brands_preserve_scope_across_workflow(self):
        from playwright.sync_api import sync_playwright,expect
        third=deepcopy(self.scenario.state['groups']['other']);third['ref']['details']['brand']='Gamma'
        self.scenario.state['groups']['third']=third;self.scenario.save(update_fields=['state'])
        with sync_playwright() as driver:
            browser=driver.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'),headless=True)
            context=browser.new_context(viewport={'width':1700,'height':1000})
            context.add_cookies([{'name':'sessionid','value':self.client.cookies['sessionid'].value,'url':self.live_server_url}])
            page=context.new_page()
            try:
                page.goto(self.live_server_url+self.url+'?tab=plan')
                picker=page.locator('[data-brand-multiselect]');picker.locator('summary').click()
                picker.locator('[data-brand-search]').fill('Beta')
                expect(picker.locator('[data-brand-choice=alpha]')).not_to_be_visible()
                picker.locator('[data-brand-choice=beta]').check()
                with page.expect_navigation():picker.locator('[data-brands-apply]').click()
                expect(page.locator('[data-plan-group]')).to_have_count(2)
                expect(page.locator('[data-brand-multiselect] summary')).to_contain_text('Выбрано: 2')
                with page.expect_navigation():page.get_by_role('link',name='К распределению →',exact=True).click()
                picker=page.locator('[data-brand-multiselect]');picker.locator('summary').click()
                expect(picker.locator('[data-brand-choice=alpha]')).to_be_checked()
                expect(picker.locator('[data-brand-choice=beta]')).to_be_checked()
                expect(picker.locator('[data-brand-choice=gamma]')).not_to_be_checked()
                picker.locator('[data-brands-none]').click();picker.locator('[data-brands-apply]').click()
                expect(picker.locator('[data-brand-error]')).to_be_visible()
                picker.locator('[data-brands-all]').click()
                with page.expect_navigation():picker.locator('[data-brands-apply]').click()
                expect(page.locator('[data-brand-pages]')).to_have_attribute('data-brand-pages','_all_')
            finally:context.close();browser.close()
