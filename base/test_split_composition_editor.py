from copy import deepcopy
import importlib.util,os
from unittest import skipUnless
from django.test import TestCase
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from .test_split_brand_pages import setup_brands

class CompositionEditorTests(TestCase):
    setUp=setup_brands
    def post(self,**extra):
        return self.client.post(self.url+'?tab=composition&brand_page=alpha',{'action':'composition_edit','revision':self.scenario.revision,'channels':list(self.scenario.state['channels']),'volume_mode':'period',**extra},HTTP_X_REQUESTED_WITH='XMLHttpRequest')
    def test_editor_scopes_table_but_preserves_other_brands_and_guards_stock(self):
        response=self.client.get(self.url,{'tab':'composition','brand_page':'alpha'})
        self.assertEqual([row['id'] for row in response.context['composition_rows']],[self.gid])
        self.assertNotContains(response,'Показатели таблиц')
        before=deepcopy(self.scenario.state['groups'])
        for group in before.values():group.setdefault('archived_lines',{})
        self.assertEqual(self.post().status_code,200)
        self.scenario.refresh_from_db();self.assertEqual(self.scenario.state['groups'],before)
        self.assertEqual(self.post(removed_groups=[self.gid]).status_code,400)
        for power in self.scenario.state['groups'][self.gid]['powers'].values():power.update(stock_indoor='0',stock_outdoor='0')
        self.scenario.save(update_fields=['state'])
        self.assertEqual(self.post(removed_groups=[self.gid]).status_code,200)
        self.scenario.refresh_from_db();self.assertEqual(set(self.scenario.state['groups']),{'other'})
        self.assertIn(self.gid,self.scenario.state['archived_groups'])
        self.assertEqual(self.post(added_groups=[self.gid]).status_code,200)
        self.scenario.refresh_from_db();self.assertEqual(set(self.scenario.state['groups']),{self.gid,'other'})
        self.assertEqual(self.post(revision=0).status_code,400)
        self.scenario.status='approved';self.scenario.save(update_fields=['status'])
        self.assertEqual(self.post().status_code,400)

@skipUnless(os.environ.get('RUN_BROWSER_TESTS')=='1' and importlib.util.find_spec('playwright'),'Requires Chromium')
class CompositionEditorBrowserTests(StaticLiveServerTestCase):
    setUp=setup_brands
    def test_search_parameters_and_save_before_brand_navigation(self):
        from playwright.sync_api import sync_playwright,expect
        with sync_playwright() as driver:
            browser=driver.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'),headless=True)
            context=browser.new_context(viewport={'width':1700,'height':1100})
            try:
                context.add_cookies([{'name':'sessionid','value':self.client.cookies['sessionid'].value,'url':self.live_server_url}])
                page=context.new_page();page.goto(self.live_server_url+self.url+'?tab=composition&brand_page=alpha')
                expect(page.locator('.split-composition-table tbody tr')).to_have_count(1)
                page.get_by_role('button',name='Добавить группы',exact=True).click()
                expect(page.get_by_role('dialog',name='Добавить группы',exact=True)).to_be_visible()
                page.get_by_role('button',name='Закрыть',exact=True).click()
                page.locator('[name=volume_mode]').select_option('annual')
                expect(page.locator('.split-unsaved-dock')).to_be_visible()
                picker=page.locator('[data-brand-multiselect]');picker.locator('summary').click()
                picker.locator('[data-brand-choice=alpha]').uncheck();picker.locator('[data-brand-choice=beta]').check()
                picker.locator('[data-brands-apply]').click()
                expect(page.locator('.split-unsaved-dialog')).to_be_visible()
                with page.expect_navigation():page.get_by_role('button',name='Сохранить и перейти',exact=True).click()
                expect(page.locator('[data-brand-pages]')).to_have_attribute('data-brand-pages','beta')
                expect(page.locator('[name=volume_mode]')).to_have_value('annual')
                page.get_by_role('searchbox',name='Поиск групп в плане',exact=True).fill('missing')
                expect(page.locator('.split-composition-table tbody tr')).not_to_be_visible()
                page.get_by_role('searchbox',name='Поиск групп в плане',exact=True).fill('Beta')
                expect(page.locator('.split-composition-table tbody tr')).to_be_visible()
                page.screenshot(path='/tmp/split-composition-editor.png',full_page=True)
            finally:context.close();browser.close()
        self.scenario.refresh_from_db();self.assertEqual(len(self.scenario.state['groups']),2)
