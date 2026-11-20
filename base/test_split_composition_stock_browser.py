import importlib.util
import os
from unittest import skipUnless
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from .test_split_brand_pages import setup_brands


@skipUnless(os.environ.get('RUN_BROWSER_TESTS')=='1' and importlib.util.find_spec('playwright'),'Requires Chromium')
class CompositionStockBrowserTests(StaticLiveServerTestCase):
    setUp=setup_brands

    def test_individual_and_visible_bulk_exclusion_warn_cancel_confirm_and_archive(self):
        from playwright.sync_api import sync_playwright,expect
        with sync_playwright() as driver:
            browser=driver.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'),headless=True)
            context=browser.new_context(viewport={'width':1700,'height':1100})
            context.add_cookies([{'name':'sessionid','value':self.client.cookies['sessionid'].value,'url':self.live_server_url}])
            page=context.new_page();errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
            try:
                page.goto(self.live_server_url+self.url+'?tab=composition&brand_page=_all_')
                expect(page.locator('[data-composition-stock]').first).to_contain_text('200')
                single=page.locator('[name=removed_groups][value="'+self.gid+'"]')
                expect(single).to_be_enabled();single.check();single.uncheck()
                search=page.get_by_role('searchbox',name='Поиск групп в плане',exact=True)
                search.fill('Beta')
                page.get_by_role('checkbox',name='Исключить все показанные группы',exact=True).check()
                expect(page.locator('[name=removed_groups][value=other]')).to_be_checked()
                expect(single).not_to_be_checked()
                search.fill('')
                expect(page.locator('[data-composition-remove-all]')).to_have_js_property('indeterminate',True)
                single.check()
                expect(page.locator('[data-composition-removal-count]')).to_have_text('Исключено: 2 групп · с остатками: 2')
                page.get_by_role('button',name='Сохранить состав плана',exact=True).click()
                dialog=page.get_by_role('dialog',name='Исключение групп с остатками',exact=True)
                expect(dialog).to_be_visible();expect(dialog.locator('li')).to_have_count(2)
                page.screenshot(path='/tmp/composition-stock-warning.png',full_page=False)
                dialog.get_by_role('button',name='Вернуться к составу',exact=True).click()
                expect(dialog).not_to_be_visible();expect(single).to_be_checked()
                # Confirmation is also required on the shared save-and-next route.
                page.locator('[data-split-save-next]').click()
                expect(dialog).to_be_visible()
                with page.expect_navigation():
                    dialog.get_by_role('button',name='Подтвердить исключение и сохранить',exact=True).click()
                self.assertIn('section=months',page.url)
                expect(page.locator('.split-workflow-step[aria-current]')).to_contain_text('Сезонность')
                self.assertEqual(errors,[])
            finally:context.close();browser.close()
        self.scenario.refresh_from_db()
        self.assertFalse(self.scenario.state['groups'])
        self.assertEqual(set(self.scenario.state['archived_groups']),{self.gid,'other'})
        self.assertEqual(self.scenario.state['archived_groups'][self.gid]['powers']['07']['stock_indoor'],'100')
