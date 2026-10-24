import importlib.util
import os
from unittest import skipUnless
from django.contrib.auth.models import User
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.conf import settings
from django.urls import reverse
from base.testing import authorize_test_case
from .access import FINANCIER, ANALYST, ADMIN, ensure_roles


@skipUnless(os.environ.get('RUN_BROWSER_TESTS') == '1' and importlib.util.find_spec('playwright'), 'Optional Chromium check')
class ProjectPermissionsBrowserTests(StaticLiveServerTestCase):
    def test_admin_assigns_independent_projects_and_previews_combined_access(self):
        from playwright.sync_api import sync_playwright, expect
        authorize_test_case(self); ensure_roles()
        with sync_playwright() as driver:
            browser=driver.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'),headless=True)
            page=browser.new_page(viewport={'width':1440,'height':1050})
            page.context.add_cookies([{'name':settings.SESSION_COOKIE_NAME,'value':self.client.cookies[settings.SESSION_COOKIE_NAME].value,'url':self.live_server_url}])
            errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
            page.goto(self.live_server_url+reverse('portal_users'))
            page.locator('#id_username').fill('DOMAIN\\projects-browser')
            page.locator('#id_motivation_role').select_option(FINANCIER)
            page.locator('#id_purchases_role').select_option('')
            page.locator('#id_dashboards_role').select_option(ANALYST)
            page.get_by_role('button',name='Добавить пользователя',exact=True).click()
            card=page.locator('.user-card').filter(has_text='domain\\projects-browser')
            expect(card).to_contain_text('Мотивация: Финансист')
            expect(card).to_contain_text('Дашборды: Аналитик')
            card.get_by_role('button',name='Посмотреть как пользователь',exact=True).click()
            expect(page.locator('.portal-project-card')).to_have_count(2)
            response=page.goto(self.live_server_url+reverse('dashboards_index'))
            self.assertEqual(response.status,200)
            expect(page.locator('.portal-user')).to_contain_text('Аналитик')
            response=page.goto(self.live_server_url+reverse('global_coeff_admin_motivation'))
            self.assertEqual(response.status,403)
            response=page.goto(self.live_server_url+reverse('motivation_discount_segments'))
            self.assertEqual(response.status,200)
            expect(page.locator('.viewer-notice')).to_contain_text('Финансист')
            page.get_by_role('button',name='Вернуться к администратору',exact=True).click()
            page.goto(self.live_server_url+reverse('portal_user_groups'))
            page.locator('#id_name').fill('Dashboards administrators')
            page.locator('#id_motivation_role').select_option('')
            page.locator('#id_purchases_role').select_option('')
            page.locator('#id_dashboards_role').select_option(ADMIN)
            page.get_by_role('button',name='Создать группу',exact=True).click()
            expect(page.locator('[data-group-card]').filter(has_text='Dashboards administrators')).to_contain_text('Дашборды: Администратор')
            page.screenshot(path='/tmp/project-permissions-groups.png',full_page=True)
            self.assertEqual(errors,[])
            browser.close()
