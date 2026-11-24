import importlib.util
import os
from pathlib import Path
from unittest import skipUnless
from django.conf import settings
from django.test import TestCase
from django.urls import reverse
from . import test_curators as fixtures


@skipUnless(os.environ.get('RUN_BROWSER_TESTS')=='1' and importlib.util.find_spec('playwright'),'Browser checks are opt-in')
class CuratorBrowserTests(TestCase):
    setUp=fixtures.CuratorTests.setUp
    save=fixtures.CuratorTests.save

    def test_physical_assignments_rate_prefill_and_excel_controls(self):
        self.save(self.b,'5')
        responses=[('directory',self.client.get(reverse('motivation_curators'),{'employee':self.employee.pk})),
            ('rates',self.client.get(reverse('motivation_curator_rates',args=[self.employee.pk]),{'version':self.version.pk,'subdivision':self.b.pk,'goods':self.good.pk}))]
        css=(Path(settings.BASE_DIR)/'admin_motivation/static/admin_motivation/curators.css').read_text()
        from playwright.sync_api import sync_playwright,expect
        with sync_playwright() as pw:
            browser=pw.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE') or None,headless=True)
            try:
                page=browser.new_page(viewport={'width':1300,'height':900})
                for name,response in responses:
                    self.assertEqual(response.status_code,200)
                    page.set_content(response.content.decode())
                    html=page.locator('.cu-page').evaluate('el=>el.outerHTML')
                    page.set_content('<style>body{font:14px system-ui,sans-serif;margin:24px;background:#f4f5fa}'+css+'</style>'+html)
                    if name=='directory':
                        self.assertTrue(page.locator(f'select[name=subdivisions] option[value="{self.b.pk}"]').evaluate('el=>el.selected'))
                        expect(page.get_by_role('button',name='Сохранить куратора и назначения')).to_be_visible()
                    else:
                        expect(page.get_by_role('button',name='Загрузить Excel')).to_be_visible()
                        self.assertEqual(float(page.locator('[name=k0]').input_value()),5)
                        self.assertEqual(page.locator('select[name=subdivision]').input_value(),str(self.b.pk))
                    self.assertTrue(page.evaluate('document.documentElement.scrollWidth<=innerWidth'))
                    page.screenshot(path='/tmp/curators-'+name+'.png',full_page=True)
            finally:browser.close()
