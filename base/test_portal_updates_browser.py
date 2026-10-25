import importlib.util
import json
import os
from pathlib import Path
import tempfile
from concurrent.futures import ThreadPoolExecutor
from unittest import skipUnless
from unittest.mock import patch
from django.conf import settings
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import override_settings
from django.urls import reverse
from base.testing import authorize_admin
from scripts import portal_update_state as state

ROOT = Path(__file__).resolve().parent.parent


@skipUnless(os.environ.get('RUN_BROWSER_TESTS') == '1' and importlib.util.find_spec('playwright'), 'Optional Chromium check')
class PortalUpdateBrowserTests(StaticLiveServerTestCase):
    def test_progress_survives_maintenance_page_and_missing_django_session(self):
        from playwright.sync_api import sync_playwright, expect
        with tempfile.TemporaryDirectory() as directory, ThreadPoolExecutor(max_workers=1) as pool:
            root = Path(directory)
            state.write(root / '.local' / 'browser-updates.json', {'root': str(root), 'task_name': 'Portal-Updater', 'portal_task_name': 'Portal'})
            pool.submit(lambda: authorize_admin(self.client)).result()
            def launch(run_id):
                state.update(root, run_id, status='running', maintenance=True)
                return state.progress(root, run_id, 'prepare', 'Копирование резервной копии: 40%')
            with override_settings(BASE_DIR=root), patch('base.portal_updates.windows_supported', return_value=True), patch('base.portal_updates.revision', return_value='a' * 40), patch('base.portal_updates.launch', side_effect=launch) as start_task, sync_playwright() as driver:
                browser = driver.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'), headless=True)
                page = browser.new_page(viewport={'width': 1440, 'height': 1000})
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.context.add_cookies([{'name': settings.SESSION_COOKIE_NAME, 'value': self.client.cookies[settings.SESSION_COOKIE_NAME].value, 'url': self.live_server_url}])
                page.goto(self.live_server_url + reverse('portal_updates'))
                page.get_by_role('button', name='Обновить портал', exact=True).click()
                expect(page.locator('#update-action-error')).to_contain_text('Подтвердите')
                start_task.assert_not_called()
                page.locator('#update-confirm').check()
                page.get_by_role('button', name='Обновить портал', exact=True).click()
                expect(page.locator('#update-detail')).to_contain_text('40%')
                record = state.active(root)
                credentials = page.evaluate('(key) => JSON.parse(sessionStorage.getItem(key))', 'portal-update:' + record['id'])
                self.assertTrue(state.authorized(root, record['id'], credentials['token']))
                self.assertTrue(state.authorized(root, record['id'], credentials['control'], control=True))
                start_task.assert_called_once_with(record['id'])
                expect(page.locator('#update-start')).to_be_disabled()
                page.screenshot(path='/tmp/portal-browser-update-progress.png', full_page=True)
                page.wait_for_load_state('networkidle')
                # After shutdown the same address serves a standalone HTML shell.
                html = (ROOT / 'scripts' / 'portal_update_maintenance.html').read_text().replace('data-maintenance="true"', f'data-maintenance="true" data-run="{record["id"]}"')
                page.route('**/accounts/updates/', lambda route: route.fulfill(status=503, body=html, content_type='text/html'))
                page.route('**/accounts/updates/maintenance.js', lambda route: route.fulfill(body=(ROOT / 'base' / 'static' / 'base' / 'portal_updates.js').read_text(), content_type='text/javascript'))
                page.context.clear_cookies()
                page.reload()
                expect(page.locator('#update-detail')).to_contain_text('40%')
                page.set_viewport_size({'width': 390, 'height': 844})
                self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'), 390)
                state.progress(root, record['id'], 'complete')
                state.update(root, record['id'], status='completed', maintenance=False, finished_at=state.utcnow())
                expect(page.locator('#update-stage')).to_have_text('Портал готов', timeout=10000)
                expect(page.locator('#update-progress')).to_have_attribute('value', '100')
                expect(page.locator('#update-open')).to_be_visible()
                self.assertEqual(errors, [])
                page.wait_for_load_state('networkidle')
                browser.close()
