import os,importlib.util,re,time
from unittest import skipUnless
from unittest.mock import patch
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.urls import reverse
from . import test_versions_browser as fixtures
from .test_coefficient_iteration_calculation import CalculationTests
from .models import CoefficientIterationRun


@skipUnless(os.environ.get('RUN_BROWSER_TESTS')=='1' and importlib.util.find_spec('playwright'),'Optional Chromium check')
class IterationCalculationBrowserTests(StaticLiveServerTestCase):
    setUp=fixtures.GlobalVersionBrowserTests.setUp
    db=fixtures.GlobalVersionBrowserTests.db

    def test_plan_selection_progress_results_and_excel(self):
        from playwright.sync_api import expect
        def prepare():
            from django.test import Client
            case=CalculationTests();case.client=Client();case.setUp()
            return case.iteration.pk,{year:plan.pk for year,plan in case.plans.items()}
        iteration,plans=self.db(prepare)
        self.page.goto(self.live_server_url+reverse('motivation_coefficient_iteration_mapping',args=[iteration]))
        self.page.get_by_role('link',name='Выбрать планы и запустить расчёт →').click()
        expect(self.page.get_by_role('heading',name='Расчёты новой итерации',exact=False)).to_be_visible()
        self.page.locator('input[name=scenarios][value=actual_2025]').uncheck()
        for year in (2026,2027):self.page.locator(f'#id_plan_{year}').select_option(str(plans[year]),force=True)
        from .services.coefficient_iteration_calculation import at_full_plan
        def slow(*args,**kwargs):time.sleep(.5);return at_full_plan(*args,**kwargs)
        with patch('admin_motivation.services.coefficient_iteration_runner.at_full_plan',side_effect=slow):
            button=self.page.get_by_role('button',name='Рассчитать выбранные сценарии')
            button.click();expect(button).to_be_disabled()
            expect(self.page.locator('#director-progress')).to_be_visible()
            self.page.wait_for_url(re.compile(r'.*\?run=\d+'),timeout=30000)
        expect(self.page.get_by_role('heading',name='Запуск №',exact=False)).to_be_visible()
        expect(self.page.get_by_role('heading',name='Москва + ТТ · IV квартал 2026 · 100%')).to_be_visible()
        expect(self.page.get_by_role('heading',name='Все подразделения · 2027 · 100%')).to_be_visible()
        expect(self.page.locator('.iteration-table').first).to_contain_text('Все выбранные подразделения')
        with self.page.expect_download() as event:
            self.page.get_by_role('link',name='Скачать результаты Excel').click()
        self.assertTrue(event.value.suggested_filename.endswith('.xlsx'))
        self.assertEqual(self.db(lambda:CoefficientIterationRun.objects.get(iteration_id=iteration).state),'completed')
        self.assertEqual(self.errors,[])
        self.page.screenshot(path='/workspace/output/coefficient-iteration-results.png',full_page=True)
