import importlib.util
import os
from datetime import date
from unittest import skipUnless
from unittest.mock import patch
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.conf import settings
from django.urls import reverse
from base.testing import authorize_test_case
from .tests import fixture


@skipUnless(os.environ.get('RUN_BROWSER_TESTS') == '1' and importlib.util.find_spec('playwright'), 'Optional Chromium check')
class DealerDashboardBrowserTests(StaticLiveServerTestCase):
    def test_six_pages_filters_dealer_and_recovery_on_desktop_and_mobile(self):
        from playwright.sync_api import sync_playwright, expect
        authorize_test_case(self)
        source={'rows':fixture(),'loaded_at':'2026-10-24T10:00:00Z','end_exclusive':'2026-10-01'}
        with sync_playwright() as driver, patch('dashboards.views.load_sales',return_value=source) as load:
            browser=driver.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'),headless=True)
            page=browser.new_page(viewport={'width':1440,'height':1050})
            page.context.add_cookies([{'name':settings.SESSION_COOKIE_NAME,'value':self.client.cookies[settings.SESSION_COOKIE_NAME].value,'url':self.live_server_url}])
            errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
            page.goto(self.live_server_url+reverse('dealer_dashboard')+'?period_mode=calendar')
            expect(page.locator('#dashboard-result')).to_be_visible()
            expect(page.locator('#dashboard-error')).not_to_be_visible()
            expect(page.locator('.dash-kpi')).to_have_count(4)
            expect(page.locator('#dealer-share-chart .dash-bar-row')).to_have_count(5)
            page.screenshot(path='/tmp/dealer-dashboard-overview.png',full_page=True)
            for tab in ('flows','bridge','cohorts','branches','dealer'):
                page.locator(f'[data-tab="{tab}"]').click()
                expect(page.locator(f'[data-panel="{tab}"]')).to_be_visible()
            page.locator('[data-tab="flows"]').click()
            expect(page.locator('#sankey-chart svg')).to_be_visible()
            expect(page.locator('#transition-matrix th').first).to_have_text('Из / в')
            page.locator('[data-tab="bridge"]').click()
            expect(page.locator('#bridge-check')).to_contain_text('Мост сходится')
            page.locator('[data-tab="dealer"]').click()
            page.locator('#dealer-choice').select_option('grow')
            page.get_by_role('button',name='Открыть',exact=True).click()
            expect(page.locator('#dealer-card h3')).to_contain_text('grow')
            expect(page.locator('#dealer-volume-chart svg')).to_be_visible()
            expect(page.locator('#dealer-timeline')).to_contain_text('XL')
            page.locator('select[name=method]').select_option('abc')
            page.get_by_role('button',name='Применить',exact=True).click()
            expect(page.locator('#dashboard-result')).to_be_visible()
            expect(page.locator('#dealer-timeline')).to_contain_text('A')
            page.locator('[data-tab="overview"]').click()
            page.set_viewport_size({'width':390,'height':844})
            self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'),390)
            page.screenshot(path='/tmp/dealer-dashboard-mobile.png',full_page=True)
            load.side_effect=RuntimeError('Unavailable')
            page.get_by_role('button',name='Применить',exact=True).click()
            expect(page.locator('#dashboard-error')).to_be_visible()
            expect(page.locator('#dashboard-result')).not_to_be_visible()
            load.side_effect=None
            page.get_by_role('button',name='Повторить',exact=True).click()
            expect(page.locator('#dashboard-result')).to_be_visible()
            self.assertEqual(errors,[])
            browser.close()
