from base.testing import authorize_test_case
"""Run with RUN_BROWSER_TESTS=1; see README for Chromium installation."""
import importlib.util
import os
from concurrent.futures import ThreadPoolExecutor
from unittest import skipUnless
from unittest.mock import patch

from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.urls import reverse
from django.test import override_settings
from .models import ScenarioModel, ScenarioExport, AlgorithmRun, Purch, PurchPay, KindLagPay, KindPurch, PGGoods, Freight
from .services.preflight import snapshot, fingerprint

BROWSER_ENABLED = os.environ.get('RUN_BROWSER_TESTS') == '1' and importlib.util.find_spec('playwright') is not None


@skipUnless(BROWSER_ENABLED, 'Enable RUN_BROWSER_TESTS=1 and install requirements-test.txt to run Chromium checks.')
class PurchaseBrowserTests(StaticLiveServerTestCase):
    def setUp(self):
        authorize_test_case(self)
        self.source = ScenarioModel.objects.create(name='Source', date_start_plan='2026-01-01', date_end_plan='2026-12-31')
        self.target = ScenarioModel.objects.create(name='Target', date_start_plan='2027-01-01', date_end_plan='2027-12-31')
        self.purchase = Purch.objects.create(name='Supplier', lag_income=90, lage_make=35)
        kind = KindLagPay.objects.create(name='Delivery')
        PurchPay.objects.create(purch=self.purchase, name='Payment', percent_pay=100, lag_day_pay=10, kind_lag_pay=kind)
        goods_kind = KindPurch.objects.create(name='Purchased')
        self.good = PGGoods.objects.create(scenario_plan=self.source, planning_group='Group', planning_sales='Sales',
            group_goods='Goods', purch='Supplier', kind_purch=goods_kind, volume=1, exw_usd=10, ddp_usd=15, kddp=1.5,
            stock_cnt_day=30, percent_stock_end=20)
        Freight.objects.create(scenario=self.source, price_per_container='1234.56', volume_per_container='67.890')

        from playwright.sync_api import sync_playwright
        self.playwright = sync_playwright().start()
        self.addCleanup(self.playwright.stop)
        executable = os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE')
        self.browser = self.playwright.chromium.launch(executable_path=executable or None, headless=True)
        self.addCleanup(self.browser.close)
        self.page = self.browser.new_page()
        from django.conf import settings
        self.page.context.add_cookies([{'name': settings.SESSION_COOKIE_NAME, 'value': self.client.cookies[settings.SESSION_COOKIE_NAME].value, 'url': self.live_server_url}])
        self.errors = []
        self.asset_events = []
        self.page.on("response", lambda response: self.asset_events.append((response.status, response.url)) if "bootstrap.bundle" in response.url else None)
        self.page.on("requestfailed", lambda request: self.asset_events.append((request.failure, request.url)))
        self.page.on('pageerror', lambda error: self.errors.append(error.stack))
        self.database_pool = ThreadPoolExecutor(max_workers=1)
        self.addCleanup(self.database_pool.shutdown)

    def db(self, operation):
        # Playwright's sync driver owns a loop in this thread; Django ORM runs
        # in a separate synchronous thread, never with async-safety disabled.
        return self.database_pool.submit(operation).result()

    def visit(self, name):
        self.page.goto(self.live_server_url + reverse(name))

    def select(self, scenario):
        with self.page.expect_navigation():
            self.page.locator('#workspace-scenario').select_option(str(scenario.pk))

    def test_shared_purchase_edit_is_visible_across_scenarios(self):
        self.visit('pggoods_list')
        self.select(self.target)
        self.page.get_by_role('link', name='Закупки', exact=True).click()
        self.assertEqual(self.page.locator('#workspace-scenario').count(), 0)
        self.assertTrue(self.page.locator(f'#purchase-{self.purchase.pk}').is_visible())
        self.page.locator(f'#purchase-{self.purchase.pk} a[title="Редактировать"]').click()
        self.page.locator('[name="lag_income"]').fill('77')
        with self.page.expect_navigation():
            self.page.get_by_role('button', name='Сохранить', exact=True).click()
        self.assertEqual(self.db(lambda: Purch.objects.get(pk=self.purchase.pk).lag_income), 77)
        self.page.get_by_role('link', name='Товары', exact=True).click()
        self.assertEqual(self.page.locator('#workspace-scenario').input_value(), str(self.target.pk))
        self.select(self.source)
        self.page.get_by_role('link', name='Закупки', exact=True).click()
        self.assertIn('77', self.page.locator(f'#purchase-{self.purchase.pk}').inner_text())
        self.assertEqual(self.errors, [], self.asset_events)

    def test_copy_goods_through_modal_and_preserve_selected_scenario(self):
        self.visit('pggoods_list')
        self.select(self.target)
        self.page.locator('[data-bs-target="#copyPggoodsModal"]').click()
        modal = self.page.locator('#copyPggoodsModal')
        modal.wait_for(state='visible')
        modal.locator('select[name="source_scenario_id"]').select_option(str(self.source.pk))
        self.assertIn('Из: Source', modal.locator('.copy-direction').inner_text())
        self.assertIn('В: Target', modal.locator('.copy-direction').inner_text())
        with self.page.expect_navigation():
            modal.locator('button[type="submit"]').click()
        self.assertEqual(self.db(lambda: PGGoods.objects.get(scenario_plan=self.target).planning_group), 'Group')
        self.assertEqual(self.page.locator('#workspace-scenario').input_value(), str(self.target.pk))
        self.assertEqual(self.errors, [], self.asset_events)

    def test_save_and_copy_freight_through_forms(self):
        self.visit('freight')
        self.select(self.target)
        self.page.locator('[name="price_per_container"]').fill('2000.25')
        self.page.locator('[name="customs_rate"]').fill('7.25')
        self.page.locator('[name="warehouse_delivery_cost"]').fill('456.78')
        self.assertEqual(self.page.locator('[name="volume_per_container"]').count(), 0)
        with self.page.expect_navigation():
            self.page.get_by_role('button', name='Сохранить', exact=True).click()
        self.assertEqual(self.db(lambda: str(Freight.objects.get(scenario=self.target).price_per_container)), '2000.25')
        self.assertEqual(self.db(lambda: str(Freight.objects.get(scenario=self.target).customs_rate)), '7.25')
        self.assertEqual(self.db(lambda: str(Freight.objects.get(scenario=self.target).warehouse_delivery_cost)), '456.78')
        self.page.locator('#freight-source').select_option(str(self.source.pk))
        with self.page.expect_navigation():
            self.page.get_by_role('button', name='Копировать', exact=True).click()
        self.assertEqual(self.db(lambda: str(Freight.objects.get(scenario=self.target).price_per_container)), '1234.56')
        self.assertEqual(self.db(lambda: str(Freight.objects.get(scenario=self.target).volume_per_container)), '65.000')
        self.assertEqual(self.errors, [], self.asset_events)

    def test_bulk_export_select_all_and_submit_selected_scenarios(self):
        self.visit('scenario_list')
        button = self.page.locator('#bulk-export-button')
        self.assertFalse(button.is_enabled())
        self.page.locator('#select-all-scenarios').check()
        self.assertTrue(button.is_enabled())
        with patch('model_purch.views.MS_SQL_CONN_STR', 'test'), patch(
            'model_purch.views._export_scenario_to_sql', return_value=True,
        ) as export:
            with self.page.expect_navigation():
                button.click()
            self.assertEqual({call.args[1].pk for call in export.call_args_list}, {self.source.pk, self.target.pk})
        self.assertIn('Успешно: 2 из 2', self.page.locator('#messagesContainer').inner_text())
        self.assertEqual(self.errors, [], self.asset_events)

    @override_settings(MS_SQL_CONN_STR='test')
    def test_calculation_start_includes_all_scenarios_and_saved_exports(self):
        from .services.copying import copy_goods
        self.db(lambda: copy_goods(self.source, self.target))
        target_parameters = self.db(lambda: snapshot(self.target))
        self.db(lambda: ScenarioExport.objects.create(scenario=self.target, parameters=target_parameters, fingerprint=fingerprint(target_parameters)))
        parameters = self.db(lambda: snapshot(self.source))
        export = self.db(lambda: ScenarioExport.objects.create(scenario=self.source, parameters=parameters,
                                                               fingerprint=fingerprint(parameters)))
        self.visit('results_page')
        self.select(self.source)
        with patch('model_purch.views._run_algorithm_step', return_value={'message': 'Test step'}) as execute:
            self.page.get_by_role('button', name='Запустить', exact=True).click()
            self.page.locator('#completedSection').wait_for(state='visible')
            self.assertEqual(execute.call_count, 5)
        run = self.db(lambda: AlgorithmRun.objects.get())
        self.assertIsNone(run.scenario_id)
        self.assertIsNone(run.scenario_export_id)
        self.assertEqual(run.status, 'completed')
        self.assertEqual(run.parameters['scope'], 'all')
        self.assertEqual({entry['scenario_id'] for entry in run.parameters['scenarios']}, {self.source.pk, self.target.pk})
        self.assertEqual(run.parameters['scenarios'][0]['parameters'], parameters)
        self.assertEqual(self.errors, [], self.asset_events)

    def test_payment_conditions_filter_and_empty_search_are_readable(self):
        def create_invalid_purchase():
            purchase = Purch.objects.create(name='Needs review', lag_income=10, lage_make=0)
            PurchPay.objects.create(purch=purchase, name='Advance', percent_pay=30, lag_day_pay=-10)
            return purchase.pk
        invalid_id = self.db(create_invalid_purchase)
        self.visit('purch_list')
        self.assertIn('100% · Payment', self.page.locator(f'#purchase-{self.purchase.pk}').inner_text())
        self.page.locator('#purchPaymentFilter').select_option('invalid')
        self.assertTrue(self.page.locator(f'#purchase-{invalid_id}').is_visible())
        self.assertFalse(self.page.locator(f'#purchase-{self.purchase.pk}').is_visible())
        self.assertIn('требуется 100%', self.page.locator(f'#purchase-{invalid_id}').inner_text())
        self.page.locator('#purchSearchInput').fill('missing supplier')
        self.page.locator('#purchSearchInput').press('End')
        self.assertTrue(self.page.locator('#purch-search-empty').is_visible())
        self.assertEqual(self.errors, [], self.asset_events)

    def test_mobile_freight_layout_does_not_overflow_viewport(self):
        self.page.set_viewport_size({'width': 390, 'height': 844})
        self.visit('freight')
        self.assertLessEqual(self.page.evaluate('document.documentElement.scrollWidth'), 390)
        self.assertTrue(self.page.locator('#workspace-scenario').is_visible())
        self.assertTrue(self.page.locator('[name="price_per_container"]').is_visible())
        self.assertEqual(self.errors, [], self.asset_events)
