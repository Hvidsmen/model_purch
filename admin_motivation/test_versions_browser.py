import importlib.util
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from unittest import skipUnless
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.urls import reverse
from .models import GlobalCoeffVersion, GlobalCoeff, Goods, SegmentCoeff, TypeCoeff, VariationCalculate, Subdivision, SubdivisionCoeff, SubdivisionManagerCoeff, Chanel, KindManagerCoeff


@skipUnless(os.environ.get('RUN_BROWSER_TESTS') == '1' and importlib.util.find_spec('playwright'), 'Optional Chromium check')
class GlobalVersionBrowserTests(StaticLiveServerTestCase):
    def setUp(self):
        self.first, _ = GlobalCoeffVersion.objects.get_or_create(effective_from=date(2001, 1, 1))
        good = Goods.objects.create(goods_key='Key', planning_group_sales='Sales', group='Group', brand='Brand')
        segment = SegmentCoeff.objects.create(segment_name='Segment')
        kind = TypeCoeff.objects.create(type_coeff_name='Политики')
        variation = VariationCalculate.objects.create(variation_name='Level')
        self.coeff = GlobalCoeff.objects.create(version=self.first, goods=good, type_coeff=kind, segment=segment,
            motivation_coeff=0.123456, manager_coeff=1, variation_calculate=variation)
        from playwright.sync_api import sync_playwright
        self.driver = sync_playwright().start()
        self.addCleanup(self.driver.stop)
        self.browser = self.driver.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'), headless=True)
        self.addCleanup(self.browser.close)
        self.page = self.browser.new_page()
        self.errors = []
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))
        self.pool = ThreadPoolExecutor(max_workers=1)
        self.addCleanup(self.pool.shutdown)

    def db(self, action):
        return self.pool.submit(action).result()

    def test_create_edit_and_read_history_without_losing_percentage_precision(self):
        self.page.goto(self.live_server_url + reverse('global_coeff_admin_motivation'))
        self.assertEqual(self.page.locator('#mainCoeffForm input[name^="global_coeff="]').input_value(), '12.3456%')
        self.page.locator('#id_effective_from').fill('2027-01-01')
        self.page.locator('#id_title').fill('January')
        with self.page.expect_navigation():
            self.page.get_by_role('button', name='Создать новую версию', exact=True).click()
        latest = self.db(lambda: GlobalCoeffVersion.objects.order_by('-effective_from').first())
        self.assertEqual(self.page.locator('#coefficient-version').input_value(), str(latest.pk))
        self.page.locator('#mainCoeffForm input[name^="global_coeff="]').fill('0.25')
        self.assertEqual(self.page.locator('#mainCoeffForm input[name^="global_coeff="]').input_value(), '0.25')
        self.page.locator('#mainCoeffForm input[name^="global_coeff="]').press('Tab')
        self.assertEqual(self.page.locator('#mainCoeffForm input[name^="global_coeff="]').input_value(), '25%')
        with self.page.expect_navigation():
            self.page.get_by_role('button', name='Сохранить изменения', exact=True).click()
        self.assertEqual(self.db(lambda: latest.coefficients.get().motivation_coeff), 0.25, self.page.locator('#version-panel').inner_text())
        self.assertEqual(self.db(lambda: GlobalCoeff.objects.get(pk=self.coeff.pk).motivation_coeff), 0.123456)
        self.page.locator('#coefficient-version').select_option(str(self.first.pk))
        with self.page.expect_navigation():
            self.page.get_by_role('button', name='Открыть', exact=True).click()
        self.assertTrue(self.page.get_by_role('button', name='Сохранить изменения', exact=True).is_disabled())
        self.assertEqual(self.page.locator('#mainCoeffForm input[name^="global_coeff="]').input_value(), '12.3456%')
        self.page.locator('#version-history summary').click()
        self.assertIn('31.12.2026', self.page.locator('#version-history').inner_text())
        self.assertEqual(self.errors, [])

    def test_shared_version_manager_and_subdivision_edits_leave_history_intact(self):
        def setup_subdivision():
            channel = Chanel.objects.create(chanel_name='Channel')
            sub = Subdivision.objects.create(subdivision_key='A', subdivision_name='A', subdivision_global='A', chanel=channel)
            kind = KindManagerCoeff.objects.create(name='Year')
            global_coeff = GlobalCoeff.objects.get(pk=self.coeff.pk)
            SubdivisionCoeff.objects.create(version=self.first, subdivision=sub, goods=global_coeff.goods,
                type_coeff=global_coeff.type_coeff, segment=global_coeff.segment, variation_calculate=global_coeff.variation_calculate,
                motivation_coeff=0.15, manager_coeff=1)
            SubdivisionManagerCoeff.objects.create(version=self.first, subdivision=sub, kind=kind, coeff=0.8)
            return sub.pk
        sub_id = self.db(setup_subdivision)
        self.page.goto(self.live_server_url + reverse('coeff_subdivisions_admin_motivation', args=[sub_id]))
        self.page.locator('#id_effective_from').fill('2027-01-01')
        with self.page.expect_navigation():
            self.page.get_by_role('button', name='Создать новую версию', exact=True).click()
        latest = self.db(lambda: GlobalCoeffVersion.objects.order_by('-effective_from').first())
        self.page.goto(self.live_server_url + reverse('coeff_subdivisions_admin_motivation', args=[sub_id]))
        self.assertEqual(self.page.locator('#coefficient-version').input_value(), str(latest.pk))
        self.page.locator('input[name^="kind_coeff="]').fill('0.9')
        self.page.locator('input[name^="kind_coeff="]').press('Tab')
        with self.page.expect_navigation():
            self.page.get_by_role('button', name='Изменить', exact=True).click()
        self.page.locator('input[name^="sub_coeff="]').fill('0.25')
        self.page.locator('input[name^="sub_coeff="]').press('Tab')
        with self.page.expect_navigation():
            self.page.get_by_role('button', name='Сохранить изменения', exact=True).click()
        self.assertEqual(self.db(lambda: latest.manager_coefficients.get().coeff), 0.9)
        self.assertEqual(self.db(lambda: latest.subdivision_coefficients.get().motivation_coeff), 0.25)
        self.assertEqual(self.db(lambda: self.first.manager_coefficients.get().coeff), 0.8)
        self.assertEqual(self.db(lambda: self.first.subdivision_coefficients.get().motivation_coeff), 0.15)
        self.page.locator('#coefficient-version').select_option(str(self.first.pk))
        with self.page.expect_navigation():
            self.page.get_by_role('button', name='Открыть', exact=True).click()
        self.assertTrue(self.page.get_by_role('button', name='Изменить', exact=True).is_disabled())
        self.assertTrue(self.page.get_by_role('button', name='Сохранить изменения', exact=True).is_disabled())
        self.assertEqual(self.errors, [])

    def test_sales_plan_create_load_calculate_and_change_scope(self):
        from unittest.mock import patch
        from .test_sales_plans import SalesPlanTests
        from .models import SalesPlanScenario
        fixtures = SalesPlanTests()
        self.db(fixtures.setUp)
        self.page.goto(self.live_server_url + reverse('motivation_sales_plans'))
        self.page.get_by_text('Создать сценарий', exact=True).click()
        self.page.locator('#id_title').fill('Browser plan')
        self.page.locator('#id_source_version').fill('2027')
        with self.page.expect_navigation():
            self.page.get_by_role('button', name='Создать', exact=True).click()
        scenario = self.db(lambda: SalesPlanScenario.objects.get(title='Browser plan'))
        with patch('admin_motivation.services.sales_plans.connect_database', fixtures.connector):
            with self.page.expect_navigation():
                self.page.get_by_role('button', name='Загрузить план из MS SQL', exact=True).click()
            with self.page.expect_navigation():
                self.page.get_by_role('button', name='Рассчитать мотивацию', exact=True).click()
        self.assertEqual(self.db(lambda: scenario.lines.get(subdivision='').total_usd), 225)
        self.assertEqual(self.page.locator('.report-node[data-level="0"]').count(), 1)
        self.page.get_by_role('button', name='Развернуть всё', exact=True).click()
        self.assertEqual(self.page.locator('.report-leaf').count(), 1)
        with self.page.expect_navigation():
            self.page.get_by_role('link', name='По подразделениям', exact=True).click()
        self.assertEqual(self.page.locator('.report-node[data-level="0"]').count(), 2)
        with self.page.expect_download() as download:
            self.page.get_by_role('link', name='Скачать CSV', exact=True).click()
        self.assertIn('subdivisions.csv', download.value.suggested_filename)
        self.assertEqual(self.errors, [])

    def test_calculation_progress_is_visible_and_duplicate_click_is_blocked(self):
        from threading import Event
        from unittest.mock import patch
        from playwright.sync_api import expect
        from .test_sales_plans import SalesPlanTests
        from .services.sales_plans import calculate_plan
        fixtures = SalesPlanTests()
        self.db(fixtures.setUp)
        self.db(fixtures.load)
        release = Event()
        def slow_calculation(scenario_id, progress):
            progress({'stage': 'Расчёт тестового плана', 'percent': 42, 'processed': 1, 'total': 3})
            if not release.wait(10):
                raise RuntimeError('Progress test timed out')
            return calculate_plan(scenario_id, progress=progress)
        url = self.live_server_url + reverse('motivation_sales_plan', args=[fixtures.scenario.pk])
        self.page.goto(url)
        with patch('admin_motivation.plan_views.calculate_plan', side_effect=slow_calculation):
            try:
                self.page.get_by_role('button', name='Рассчитать мотивацию', exact=True).click()
                expect(self.page.locator('#plan-progress')).to_be_visible()
                expect(self.page.locator('#plan-progress-bar')).to_have_attribute('aria-valuenow', '42')
                expect(self.page.locator('#plan-progress-count')).to_have_text('Обработано 1 из 3 строк')
                self.assertTrue(self.page.get_by_role('button', name='Рассчитать мотивацию', exact=True).is_disabled())
                self.assertTrue(self.page.get_by_role('button', name='Загрузить план из MS SQL', exact=True).is_disabled())
                response = self.page.request.post(url, form={'action': 'calculate', 'csrfmiddlewaretoken': self.page.locator('input[name=csrfmiddlewaretoken]').first.input_value()}, headers={'Accept': 'application/x-ndjson', 'Referer': url})
                self.assertIn('уже выполняется', response.text())
                with self.page.expect_navigation():
                    release.set()
                expect(self.page.locator('#plan-progress-bar')).to_have_attribute('aria-valuenow', '100')
                self.assertEqual(self.db(lambda: fixtures.scenario.lines.get(subdivision='').total_usd), 225)
            finally:
                release.set()
        self.assertEqual(self.errors, [])

    def test_calculation_error_is_visible_and_buttons_are_reenabled(self):
        from playwright.sync_api import expect
        from .test_sales_plans import SalesPlanTests
        from .services.sales_plans import calculate_plan
        fixtures = SalesPlanTests()
        self.db(fixtures.setUp)
        self.db(fixtures.load)
        self.db(lambda: calculate_plan(fixtures.scenario.pk))
        self.db(lambda: fixtures.version.coefficients.filter(type_coeff__type_coeff_name='Политики').delete())
        self.page.goto(self.live_server_url + reverse('motivation_sales_plan', args=[fixtures.scenario.pk]))
        self.page.get_by_role('button', name='Рассчитать мотивацию', exact=True).click()
        expect(self.page.locator('#plan-progress-error')).to_be_visible()
        expect(self.page.locator('#plan-progress-error')).to_contain_text('Нет коэффициента')
        expect(self.page.get_by_role('button', name='Рассчитать мотивацию', exact=True)).to_be_enabled()
        self.assertEqual(self.db(lambda: fixtures.scenario.lines.get(subdivision='').total_usd), 225)
        self.assertEqual(self.errors, [])

    def test_period_tree_default_expansion_subtotals_filters_and_csv(self):
        from playwright.sync_api import expect
        from .test_sales_plans import SalesPlanTests
        from .services.sales_plans import calculate_plan
        fixtures = SalesPlanTests()
        self.db(fixtures.setUp)
        self.db(fixtures.load)
        self.db(lambda: calculate_plan(fixtures.scenario.pk))
        url = self.live_server_url + reverse('motivation_sales_plan', args=[fixtures.scenario.pk])
        self.page.goto(url)
        expect(self.page.locator('.report-node[data-level="0"]')).to_have_attribute('open', '')
        self.assertEqual(self.page.locator('.report-node[data-level="1"][open]').count(), 0)
        self.assertFalse(self.page.locator('.report-leaf').is_visible())
        self.page.locator('.report-node[data-level="1"] > summary').click()
        self.page.locator('.report-node[data-level="2"] > summary').click()
        expect(self.page.locator('.report-leaf')).to_be_visible()
        expect(self.page.locator('.report-leaf')).to_contain_text('225')
        self.page.get_by_role('button', name='Свернуть всё', exact=True).click()
        self.assertEqual(self.page.locator('#period-report details[open]').count(), 0)
        self.page.get_by_role('button', name='Развернуть всё', exact=True).click()
        self.assertEqual(self.page.locator('#period-report details[open]').count(), 3)
        with self.page.expect_navigation():
            self.page.get_by_role('link', name='По подразделениям', exact=True).click()
        self.assertEqual(self.page.locator('.report-node[data-level="0"]').count(), 2)
        self.assertEqual(self.page.locator('#period-report details[open]').count(), 0)
        self.page.locator('select[name=subdivision]').select_option('A')
        with self.page.expect_navigation():
            self.page.get_by_role('button', name='Применить', exact=True).click()
        self.assertEqual(self.page.locator('.report-node[data-level="0"]').count(), 1)
        expect(self.page.locator('.report-total')).to_contain_text('230')
        with self.page.expect_download() as download:
            self.page.get_by_role('link', name='Скачать CSV', exact=True).click()
        import csv
        with open(download.value.path(), encoding='utf-8-sig') as stream:
            rows = list(csv.reader(stream, delimiter=';'))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1][0], 'A')
        self.assertNotIn('Дата', rows[0])
        with self.page.expect_navigation():
            self.page.get_by_role('link', name='Сбросить', exact=True).click()
        self.assertEqual(self.page.locator('.report-node[data-level="0"]').count(), 2)
        self.assertEqual(self.errors, [])
