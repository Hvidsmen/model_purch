import importlib.util
import os
from datetime import date
from unittest import skipUnless
from django.conf import settings
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.urls import reverse
from base.testing import authorize_test_case
from .models import GlobalCoeffVersion


@skipUnless(os.environ.get('RUN_BROWSER_TESTS') == '1' and importlib.util.find_spec('playwright'), 'Optional Chromium check')
class SubdivisionScopeBrowserTests(StaticLiveServerTestCase):
    def test_latest_plan_opens_and_legacy_ventilation_disappears_from_summary(self):
        from playwright.sync_api import sync_playwright, expect
        from .test_subdivision_scope import SubdivisionScopeTests
        GlobalCoeffVersion.objects.get_or_create(effective_from=date(2001,1,1))
        fixture=SubdivisionScopeTests();fixture.setUp();fixture.legacy_ventilation()
        authorize_test_case(self)
        with sync_playwright() as driver:
            browser=driver.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'),headless=True)
            page=browser.new_page(viewport={'width':1440,'height':1000})
            page.context.add_cookies([{'name':settings.SESSION_COOKIE_NAME,'value':self.client.cookies[settings.SESSION_COOKIE_NAME].value,'url':self.live_server_url}])
            errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
            page.goto(self.live_server_url+reverse('motivation_sales_plans'))
            expect(page).to_have_url(self.live_server_url+reverse('motivation_sales_plan',args=[fixture.plan.pk]))
            expect(page.locator('#scenario-choice')).to_have_value(reverse('motivation_sales_plan',args=[fixture.plan.pk]))
            expect(page.get_by_role('button',name='Рассчитать мотивацию',exact=True)).to_be_visible()
            page.goto(self.live_server_url+reverse('motivation_plan_summary')+f'?plan={fixture.plan.pk}')
            expect(page.locator('#plan-summary')).to_be_visible()
            expect(page.get_by_text('Вентиляция',exact=True)).to_have_count(0)
            expect(page.locator('#plan-summary')).to_contain_text('225')
            page.screenshot(path='/tmp/motivation-without-ventilation.png',full_page=True)
            page.wait_for_load_state('networkidle')
            self.assertEqual(errors,[])
            browser.close()
