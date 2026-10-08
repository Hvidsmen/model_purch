import importlib.util
import os
from unittest import skipUnless
from django.contrib.auth.models import User, Group
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from .access import ADMIN, DIRECTOR, LOGISTICIAN, ensure_roles


@skipUnless(os.environ.get('RUN_BROWSER_TESTS') == '1' and importlib.util.find_spec('playwright'), 'Optional Chromium check')
class PortalLoginBrowserTests(StaticLiveServerTestCase):
    def test_login_navigation_denied_project_and_logout_for_each_role(self):
        ensure_roles()
        for index, role in enumerate((ADMIN, DIRECTOR, LOGISTICIAN)):
            user = User.objects.create_user(f'browser-role-{index}', password='browser-test-password', is_staff=role == ADMIN)
            user.groups.add(Group.objects.get(name=role))
        from playwright.sync_api import sync_playwright
        with sync_playwright() as driver:
            browser = driver.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'), headless=True)
            try:
                for index, role in enumerate((ADMIN, DIRECTOR, LOGISTICIAN)):
                    with self.subTest(role=role):
                        page = browser.new_page(viewport={'width': 390, 'height': 800})
                        page.goto(self.live_server_url + '/accounts/login/')
                        page.fill('#id_username', f'browser-role-{index}')
                        page.fill('#id_password', 'browser-test-password')
                        page.get_by_role('button', name='Войти', exact=True).click()
                        page.wait_for_url(self.live_server_url + '/')
                        self.assertEqual(page.locator('.portal-projects a').count(), 2 if role == ADMIN else 1)
                        self.assertEqual(page.locator('.portal-project-card').count(), 2 if role == ADMIN else 1)
                        if role != ADMIN:
                            denied = '/model_purch/scenarios/' if role == DIRECTOR else '/admin_motivationglobal_coeff'
                            self.assertEqual(page.goto(self.live_server_url + denied).status, 403)
                            self.assertTrue(page.get_by_role('heading', name='Нет доступа к разделу').is_visible())
                        page.get_by_role('button', name='Выйти', exact=True).click()
                        page.wait_for_url('**/accounts/login/')
                        page.goto(self.live_server_url + '/model_purch/scenarios/')
                        self.assertIn('/accounts/login/', page.url)
                        page.close()
            finally:
                browser.close()
