import importlib.util
import os
from datetime import date
from unittest import skipUnless
from django.conf import settings
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.urls import reverse
from .models import GlobalCoeffVersion, GlobalCoeff, CoefficientBalanceLog


@skipUnless(os.environ.get('RUN_BROWSER_TESTS') == '1' and importlib.util.find_spec('playwright'), 'Optional Chromium check')
class BalancingBrowserTests(StaticLiveServerTestCase):
    def test_select_manual_compensation_preview_save_and_mobile(self):
        from playwright.sync_api import sync_playwright, expect
        from base.testing import authorize_test_case
        from .test_sales_plans import SalesPlanTests
        GlobalCoeffVersion.objects.get_or_create(effective_from=date(2001,1,1))
        fixture=SalesPlanTests();fixture.setUp();fixture.load();authorize_test_case(self)
        manual=GlobalCoeff.objects.get(version=fixture.version,type_coeff__type_coeff_name='Политики',segment__segment_name='K0')
        auto=GlobalCoeff.objects.get(version=fixture.version,type_coeff__type_coeff_name='Политики',segment__segment_name='K1')
        with sync_playwright() as driver:
            browser=driver.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'),headless=True)
            page=browser.new_page(viewport={'width':1440,'height':1000})
            page.context.add_cookies([{'name':settings.SESSION_COOKIE_NAME,'value':self.client.cookies[settings.SESSION_COOKIE_NAME].value,'url':self.live_server_url}])
            errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
            page.goto(self.live_server_url+reverse('global_coeff_admin_motivation'))
            page.get_by_role('link',name='Балансировка премии',exact=True).click()
            page.locator('[name="plan"]').select_option(str(fixture.scenario.pk))
            page.get_by_role('button',name='Загрузить исходные значения',exact=True).click()
            expect(page.locator('#balance-workspace')).to_be_visible()
            expect(page.locator('#balance-cells thead tr')).to_have_count(2)
            page.get_by_label(f'Участие c:{manual.pk}',exact=True).select_option('manual')
            page.get_by_label(f'Новый процент c:{manual.pk}',exact=True).fill('20')
            page.get_by_label(f'Участие c:{auto.pk}',exact=True).select_option('auto')
            page.get_by_role('button',name='Рассчитать балансировку',exact=True).click()
            expect(page.locator('#balance-save')).to_be_enabled()
            expect(page.locator('#balance-summary-table thead tr')).to_have_count(2)
            expect(page.locator('#balance-summary tr')).to_have_count(1)
            expect(page.locator('#balance-summary tr')).to_contain_text('Компания')
            expect(page.locator('[name="subdivision"]')).not_to_be_visible()
            expect(page.locator('#balance-branch')).not_to_be_visible()
            expect(page.locator('#balance-summary tr').first.locator('td')).to_have_count(9)
            expect(page.locator('#balance-total')).to_contain_text('Разница: 0,00 USD')
            page.screenshot(path='/tmp/coefficient-balancing-desktop.png',full_page=True)
            # Editing invalidates the reviewed result immediately.
            page.get_by_label(f'Новый процент c:{manual.pk}',exact=True).fill('21')
            expect(page.locator('#balance-save')).to_be_disabled()
            page.get_by_label(f'Новый процент c:{manual.pk}',exact=True).fill('20')
            page.locator('#balance-preview').click();expect(page.locator('#balance-save')).to_be_enabled()
            page.locator('#balance-save').click()
            expect(page.locator('#balance-status')).to_contain_text('Балансировка сохранена в черновик')
            page.set_viewport_size({'width':390,'height':844})
            self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'),390)
            page.screenshot(path='/tmp/coefficient-balancing-mobile.png',full_page=True)
            self.assertEqual(errors,[])
            browser.close()
        manual.refresh_from_db();auto.refresh_from_db()
        self.assertEqual(manual.motivation_coeff,.2);self.assertEqual(auto.motivation_coeff,.05)
        self.assertEqual(CoefficientBalanceLog.objects.count(),1)

    def test_local_company_balance_saves_only_in_selected_draft(self):
        from playwright.sync_api import sync_playwright, expect
        from django.db import models
        from base.testing import authorize_test_case
        from .test_sales_plans import SalesPlanTests
        from .models import SubdivisionCoeff
        GlobalCoeffVersion.objects.get_or_create(effective_from=date(2001,1,1))
        fixture=SalesPlanTests();fixture.setUp();fixture.load();authorize_test_case(self)
        manual=SubdivisionCoeff.objects.get(version=fixture.version,subdivision=fixture.subs[0],type_coeff__type_coeff_name='Политики',segment__segment_name='K0')
        auto=SubdivisionCoeff.objects.get(version=fixture.version,subdivision=fixture.subs[1],type_coeff__type_coeff_name='Продажи',segment__segment_name='K1')
        with sync_playwright() as driver:
            browser=driver.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'),headless=True)
            page=browser.new_page(viewport={'width':1440,'height':1000})
            page.context.add_cookies([{'name':settings.SESSION_COOKIE_NAME,'value':self.client.cookies[settings.SESSION_COOKIE_NAME].value,'url':self.live_server_url}])
            page.goto(self.live_server_url+reverse('motivation_coefficient_balancing')+f'?version={fixture.version.pk}&subdivision={fixture.subs[0].pk}')
            expect(page.locator('[name="scope"]')).to_have_value('local')
            page.locator('[name="plan"]').select_option(str(fixture.scenario.pk))
            page.locator('[name="mode"]').select_option('company')
            expect(page.locator('[name="subdivision"]')).to_be_disabled()
            page.get_by_role('button',name='Загрузить исходные значения',exact=True).click()
            expect(page.locator('#balance-workspace')).to_be_visible()
            page.get_by_label(f'Участие c:{manual.pk}',exact=True).select_option('manual')
            page.get_by_label(f'Новый процент c:{manual.pk}',exact=True).fill('21')
            page.get_by_label(f'Участие c:{auto.pk}',exact=True).select_option('auto')
            page.locator('#balance-preview').click()
            save=page.get_by_role('button',name='Сохранить в черновик',exact=True)
            expect(save).to_be_enabled();expect(page.locator('#balance-total')).to_contain_text('Разница: 0,00 USD')
            save.click();expect(page.locator('#balance-status')).to_contain_text('Балансировка сохранена в черновик')
            browser.close()
        new=GlobalCoeffVersion.objects.order_by('-pk').first()
        self.assertEqual(new.pk,fixture.version.pk);self.assertEqual(new.status,'draft')
        self.assertEqual(SubdivisionCoeff.objects.get(version=new,subdivision=fixture.subs[0],type_coeff=manual.type_coeff,segment=manual.segment).motivation_coeff,.21)
        self.assertEqual(SubdivisionCoeff.objects.get(version=new,subdivision=fixture.subs[1],type_coeff=auto.type_coeff,segment=auto.segment).motivation_coeff,.02)
        manual.refresh_from_db();auto.refresh_from_db()
        self.assertEqual(manual.motivation_coeff,.21);self.assertEqual(auto.motivation_coeff,.02)

    def test_approved_version_has_no_balance_action_and_empty_page_is_explained(self):
        from playwright.sync_api import sync_playwright, expect
        from django.db import models
        from base.testing import authorize_test_case
        from .test_sales_plans import SalesPlanTests
        GlobalCoeffVersion.objects.get_or_create(effective_from=date(2001,1,1))
        fixture=SalesPlanTests();fixture.setUp();fixture.load();authorize_test_case(self)
        models.QuerySet.update(GlobalCoeffVersion.objects.filter(pk=fixture.version.pk),status='approved')
        with sync_playwright() as driver:
            browser=driver.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'),headless=True)
            page=browser.new_page()
            page.context.add_cookies([{'name':settings.SESSION_COOKIE_NAME,'value':self.client.cookies[settings.SESSION_COOKIE_NAME].value,'url':self.live_server_url}])
            errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
            page.goto(self.live_server_url+reverse('global_coeff_admin_motivation')+f'?version={fixture.version.pk}')
            page.wait_for_load_state('networkidle')
            expect(page.get_by_role('link',name='Балансировка премии',exact=True)).to_have_count(0)
            response=page.goto(self.live_server_url+reverse('motivation_coefficient_balancing')+f'?version={fixture.version.pk}')
            self.assertEqual(response.status,403)
            page.goto(self.live_server_url+reverse('motivation_coefficient_balancing'))
            expect(page.locator('#balance-setup')).to_contain_text('Нет редактируемого черновика')
            expect(page.locator('#balance-setup [name="version"]')).to_have_count(0)
            page.wait_for_load_state('networkidle')
            self.assertEqual(errors,[])
            browser.close()
