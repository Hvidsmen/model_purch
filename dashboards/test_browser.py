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
            branch_menu=page.locator('.dash-multi[data-filter="branch"]')
            branch_menu.locator('summary').click()
            branch_menu.get_by_role('button',name='Выбрать всё',exact=True).click()
            expect(branch_menu.locator('input[type=checkbox]:checked')).to_have_count(1)
            branch_menu.get_by_role('button',name='Снять всё',exact=True).click()
            expect(branch_menu.locator('input[type=checkbox]:checked')).to_have_count(0)
            branch_menu.locator('summary').click()
            page.locator('.dash-thresholds > summary').click()
            page.locator('[name=s_max]').fill('20')
            page.locator('[name=m_max]').fill('100')
            page.locator('[name=l_max]').fill('500')
            page.get_by_role('button',name='Применить',exact=True).click()
            expect(page.locator('#dashboard-result')).to_be_visible()
            expect(page.locator('#dashboard-rules')).to_contain_text('0–20')
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
            expect(page.locator('#dealer-timeline')).to_contain_text('L')
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

    def test_refresh_job_prepares_snapshot_once_and_status_updates_without_reload(self):
        from pathlib import Path
        import tempfile
        import threading
        from django.test import override_settings
        from playwright.sync_api import sync_playwright, expect
        authorize_test_case(self)
        source={'rows':fixture(),'loaded_at':'2026-10-24T10:00:00+00:00','end_exclusive':'2026-10-01'}
        gate=threading.Event()
        def fetch(*args, **kwargs):
            gate.wait(20)
            return source
        with tempfile.TemporaryDirectory() as folder, override_settings(DEALER_SNAPSHOT_DIR=folder), patch('dashboards.services.snapshots.fetch_sales',side_effect=fetch) as read, sync_playwright() as driver:
            browser=driver.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'),headless=True)
            page=browser.new_page(viewport={'width':1440,'height':1050})
            page.context.add_cookies([{'name':settings.SESSION_COOKIE_NAME,'value':self.client.cookies[settings.SESSION_COOKIE_NAME].value,'url':self.live_server_url}])
            errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
            try:
                page.goto(self.live_server_url+reverse('dashboard_refresh'))
                expect(page.locator('#snapshot-state')).to_have_text('Обновлений ещё не было')
                page.get_by_role('button',name='Обновить сейчас',exact=True).click()
                expect(page.locator('#snapshot-state')).to_have_text('Выполняется')
                expect(page.get_by_role('button',name='Обновить сейчас',exact=True)).to_be_disabled()
                gate.set()
                expect(page.locator('#snapshot-state')).to_have_text('Успешно завершено',timeout=15000)
                expect(page.locator('#snapshot-history')).to_contain_text('Успешно завершено')
                page.locator('input[name=time]').fill('08:00')
                page.locator('input[name=enabled]').check()
                page.get_by_role('button',name='Сохранить расписание',exact=True).click()
                expect(page.locator('input[name=time]')).to_have_value('08:00')
                expect(page.locator('input[name=enabled]')).to_be_checked()
                page.screenshot(path='/tmp/dealer-refresh-job.png',full_page=True)
                page.goto(self.live_server_url+reverse('dealer_dashboard'))
                expect(page.locator('#dashboard-result')).to_be_visible()
                page.locator('select[name=method]').select_option('abc')
                page.get_by_role('button',name='Применить',exact=True).click()
                expect(page.locator('#dashboard-result')).to_be_visible()
                self.assertEqual(read.call_count,1)
                self.assertEqual(errors,[])
            finally:
                gate.set();browser.close()
