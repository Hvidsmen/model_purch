import importlib.util
import json
import os
from unittest import skipUnless
from django.contrib.auth.models import User
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.conf import settings
from django.urls import reverse
from .models import UserProjectAccess


@skipUnless(os.environ.get('RUN_BROWSER_TESTS')=='1' and importlib.util.find_spec('playwright'),'Optional Chromium check')
class AdministrationUXBrowserTests(StaticLiveServerTestCase):
    def test_overview_access_dirty_state_compact_jobs_and_mobile(self):
        from playwright.sync_api import sync_playwright,expect
        from admin_motivation.models import MotivationCalculationSchedule
        admin=User.objects.create_superuser('ux-browser-admin')
        employee=User.objects.create_user('ux-browser-employee',first_name='Иван')
        UserProjectAccess.objects.create(user=employee,project='dashboards',role='Аналитик')
        job=MotivationCalculationSchedule.objects.create(name='Проверка UX',fact_year=2026,plan_version='План UX',synced=True)
        self.client.force_login(admin)
        with sync_playwright() as driver:
            browser=driver.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'),headless=True)
            page=browser.new_page(viewport={'width':1440,'height':1000})
            page.context.add_cookies([{'name':settings.SESSION_COOKIE_NAME,'value':self.client.cookies[settings.SESSION_COOKIE_NAME].value,'url':self.live_server_url}])
            errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
            state={'status':'Выполняется','history':[],'checked_at':'12:00:00','report_url':None}
            page.route('**'+reverse('motivation_calculation_monitor',args=[job.pk]),lambda route:route.fulfill(content_type='application/json',body=json.dumps(state)))
            page.goto(self.live_server_url+reverse('portal_administration'))
            expect(page.get_by_role('heading',name='Администрирование',exact=True)).to_be_visible()
            page.screenshot(path='/tmp/admin-overview-desktop.png',full_page=True)
            page.goto(self.live_server_url+reverse('portal_users')+f'?edit={employee.pk}')
            expect(page.locator('#effective-access')).to_contain_text('Дашборды: Аналитик')
            page.locator('#id_first_name').fill('Пётр')
            expect(page.locator('#access-pending-note')).to_be_visible()
            page.locator('#id_first_name').fill('Иван')
            expect(page.locator('#access-pending-note')).not_to_be_visible()
            page.screenshot(path='/tmp/admin-access-desktop.png',full_page=True)
            page.goto(self.live_server_url+reverse('motivation_calculations'))
            expect(page.locator('[data-job-status]')).to_have_text('Выполняется')
            expect(page.get_by_role('button',name='Запустить сейчас',exact=True)).to_be_disabled()
            expect(page.locator('.admin-job-details')).not_to_have_attribute('open','')
            page.locator('.admin-job-details > summary').click()
            expect(page.locator('.calculation-history > summary')).to_be_visible()
            state.update(status='Завершено',report_url=reverse('motivation_reports_next'))
            page.reload()
            expect(page.locator('[data-job-report]')).to_be_visible()
            expect(page.get_by_role('button',name='Запустить сейчас',exact=True)).to_be_enabled()
            page.screenshot(path='/tmp/admin-jobs-desktop.png',full_page=True)
            page.set_viewport_size({'width':390,'height':844})
            for name in ('portal_administration','portal_users','portal_user_groups','portal_updates','motivation_calculations','motivation_subdivision_merge'):
                page.goto(self.live_server_url+reverse(name))
                self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'),391,name)
            page.screenshot(path='/tmp/admin-maintenance-mobile.png',full_page=True)
            self.assertEqual(errors,[])
            browser.close()

    def test_sidebar_can_be_hidden_persisted_and_restored_on_desktop_and_mobile(self):
        from playwright.sync_api import sync_playwright, expect
        admin = User.objects.create_superuser('sidebar-browser-admin')
        self.client.force_login(admin)
        with sync_playwright() as driver:
            browser = driver.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'), headless=True)
            page = browser.new_page(viewport={'width':1440,'height':1000})
            page.context.add_cookies([{'name':settings.SESSION_COOKIE_NAME,'value':self.client.cookies[settings.SESSION_COOKIE_NAME].value,'url':self.live_server_url}])
            errors=[]
            page.on('pageerror',lambda error:errors.append(str(error)))
            page.goto(self.live_server_url + reverse('motivation_reports_next'))
            sidebar = page.locator('#portal-sidebar')
            toggle = page.locator('#portal-menu-toggle')
            expect(sidebar).to_be_visible()
            before = page.locator('#portal-main').bounding_box()['width']
            toggle.click()
            expect(sidebar).to_be_hidden()
            expect(toggle).to_have_text('Показать меню')
            self.assertGreater(page.locator('#portal-main').bounding_box()['width'], before + 200)
            page.reload()
            expect(sidebar).to_be_hidden()
            page.screenshot(path='/tmp/motivation-menu-hidden.png',full_page=True)
            toggle.click()
            expect(sidebar).to_be_visible()
            page.goto(self.live_server_url + reverse('scenario_list'))
            expect(sidebar).to_be_visible()
            toggle.click()
            page.set_viewport_size({'width':390,'height':844})
            expect(sidebar).to_be_hidden()
            toggle.click()
            expect(sidebar).to_be_visible()
            sidebar.locator('a').first.focus()
            page.keyboard.press('Escape')
            expect(sidebar).to_be_hidden()
            expect(toggle).to_be_focused()
            page.set_viewport_size({'width':1440,'height':1000})
            expect(sidebar).to_be_hidden()
            toggle.click()
            expect(sidebar).to_be_visible()
            self.assertEqual(errors, [])
            browser.close()
