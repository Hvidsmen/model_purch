import importlib.util
import os
from concurrent.futures import ThreadPoolExecutor
from unittest import skipUnless
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.conf import settings
from django.test import Client
from django.urls import reverse
from base.access import MODELER, FINANCIER, DIRECTOR


@skipUnless(os.environ.get('RUN_BROWSER_TESTS') == '1' and importlib.util.find_spec('playwright'), 'Optional Chromium check')
class ReviewWorkflowBrowserTests(StaticLiveServerTestCase):
    def test_modeler_finance_director_route_and_mobile(self):
        from playwright.sync_api import sync_playwright, expect
        from .test_review_workflow import ReviewWorkflowTests
        from .models import GlobalCoeffVersion
        self.pool = ThreadPoolExecutor(max_workers=1)
        self.addCleanup(self.pool.shutdown)
        self.db(lambda: ReviewWorkflowTests.setUp(self))
        with sync_playwright() as driver:
            browser = driver.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'), headless=True)
            page = browser.new_page(viewport={'width': 1440, 'height': 1050})
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            def login(role):
                client = Client()
                self.db(lambda: client.force_login(self.users[role]))
                page.context.clear_cookies()
                page.context.add_cookies([{'name': settings.SESSION_COOKIE_NAME, 'value': client.cookies[settings.SESSION_COOKIE_NAME].value, 'url': self.live_server_url}])
            login(MODELER)
            page.goto(self.live_server_url + reverse('motivation_reviews'))
            expect(page.get_by_role('button', name='Отправить финансисту')).to_be_enabled()
            expect(page.locator('[name=plan]')).to_have_value(str(self.plan.pk))
            page.locator('[name=comment]').fill('Проверить новую модель премии')
            with page.expect_navigation():
                page.get_by_role('button', name='Отправить финансисту').click()
            expect(page.get_by_text('Ожидается решение финансиста.', exact=False)).to_be_visible()
            detail_url = page.url
            page.wait_for_load_state('networkidle')
            login(FINANCIER)
            page.goto(detail_url)
            expect(page.get_by_role('button', name='Согласовать и передать генеральному директору')).to_be_enabled()
            page.locator('[name=show]').select_option('all')
            with page.expect_navigation(): page.get_by_role('button', name='Показать', exact=True).click()
            expect(page.get_by_role('cell', name='Глобально', exact=True).first).to_be_visible()
            page.locator('#review-comment').fill('Премия и ставки проверены')
            with page.expect_navigation(): page.get_by_role('button', name='Согласовать и передать генеральному директору').click()
            expect(page.get_by_text('Ожидается решение генерального директора.', exact=False)).to_be_visible()
            page.wait_for_load_state('networkidle')
            login(DIRECTOR)
            page.goto(detail_url)
            expect(page.get_by_role('button', name='Утвердить весь набор')).to_be_enabled()
            page.screenshot(path='/tmp/motivation-review-director.png', full_page=True)
            page.set_viewport_size({'width': 390, 'height': 844})
            self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'), 390)
            page.locator('#review-comment').fill('Утверждаю')
            with page.expect_navigation(): page.get_by_role('button', name='Утвердить весь набор').click()
            expect(page.get_by_text('Проверка завершена', exact=True)).to_be_visible()
            self.assertEqual(self.db(lambda: GlobalCoeffVersion.objects.get(pk=self.version.pk).status), 'approved')
            self.assertEqual(errors, [])
            page.wait_for_load_state('networkidle')
            browser.close()

    def db(self, callback):
        return self.pool.submit(callback).result()
