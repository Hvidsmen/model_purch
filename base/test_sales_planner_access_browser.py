import importlib.util
import os
from unittest import skipUnless

from django.conf import settings
from django.contrib.auth.models import User
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.urls import reverse
from django.test import Client

from .access import PLANNER
from .models import GroupProjectAccess, SalesPlanningGoods
from .testing import authorize_test_case


@skipUnless(os.environ.get('RUN_BROWSER_TESTS') == '1' and importlib.util.find_spec('playwright'), 'Optional Chromium check')
class SalesPlannerAccessBrowserTests(StaticLiveServerTestCase):
    def test_administrator_assigns_planner_responsibility_and_user_sees_project(self):
        from playwright.sync_api import sync_playwright, expect

        authorize_test_case(self)
        planner = User.objects.create_user('browser-planner')
        SalesPlanningGoods.objects.create(source_key='rac-model', name='RAC model', product_group='Бытовые кондиционеры', sales_plan_group='1. RAC')
        SalesPlanningGoods.objects.create(source_key='vrf-model', name='VRF model', product_group='Бытовые кондиционеры', sales_plan_group='2. VRF')
        planner_client = Client()
        planner_client.force_login(planner)
        with sync_playwright() as driver:
            browser = driver.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'), headless=True)
            page = browser.new_page(viewport={'width':1440, 'height':1050})
            errors = []
            page.on('pageerror', lambda error:errors.append(str(error)))
            page.context.add_cookies([{'name':settings.SESSION_COOKIE_NAME, 'value':self.client.cookies[settings.SESSION_COOKIE_NAME].value, 'url':self.live_server_url}])
            page.goto(self.live_server_url + reverse('portal_user_groups') + '?project=sales_planning')
            expect(page.locator('.access-project-tabs [aria-current="page"]')).to_have_text('Модель планирования продаж')
            page.locator('#id_name').fill('Планировщики RAC')
            page.locator('#id_sales_planning_role').select_option(PLANNER)
            scope = page.locator('[data-access-multi]').filter(has=page.locator('input[name="sales_plan_groups"]'))
            scope.locator('summary').click()
            scope.get_by_label('1. RAC', exact=True).check()
            scope.get_by_role('button', name='Готово', exact=True).click()
            members = page.locator('[data-access-multi]').filter(has=page.locator('input[name="members"]'))
            members.locator('summary').click()
            members.locator('input[value="' + str(planner.pk) + '"]').check()
            members.get_by_role('button', name='Готово', exact=True).click()
            page.get_by_role('button', name='Создать группу', exact=True).click()
            card = page.locator('[data-group-card]').filter(has_text='Планировщики RAC')
            expect(card).to_contain_text('Модель планирования продаж: Планировщик')
            expect(card).to_contain_text('1. RAC')
            card.get_by_role('link', name='Настроить →', exact=True).click()
            expect(page.locator('input[name="sales_plan_groups"][value="1. RAC"]')).to_be_checked()
            page.screenshot(path='/tmp/planner-responsibility-form.png', full_page=True)
            page.context.add_cookies([{'name':settings.SESSION_COOKIE_NAME, 'value':planner_client.cookies[settings.SESSION_COOKIE_NAME].value, 'url':self.live_server_url}])
            page.goto(self.live_server_url + reverse('index_base'))
            expect(page.locator('.portal-project-card')).to_have_count(1)
            page.locator('.portal-project-card').click()
            expect(page.locator('.portal-user')).to_contain_text(PLANNER)
            page.goto(self.live_server_url + reverse('sales_planning_goods'))
            expect(page.locator('#portal-main')).to_contain_text('RAC model')
            expect(page.locator('#portal-main')).not_to_contain_text('VRF model')
            self.assertEqual(errors, [])
            browser.close()
        grant = GroupProjectAccess.objects.get(group__name='Планировщики RAC', project='sales_planning')
        self.assertEqual(grant.sales_plan_groups, ['1. RAC'])
