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

    def test_goods_directory_refresh_button_updates_metadata_and_guards_unsaved_edits(self):
        from unittest.mock import Mock
        from playwright.sync_api import expect
        from .models import ScenarioPlanSales
        self.db(lambda: ScenarioPlanSales.objects.create(scenario_model=self.source,name='New goods plan',flag_order_in_purch=True))
        connection, cursor = Mock(), Mock()
        cursor.fetchall.side_effect = [[('group', 'New sales', 'New goods', 'New brand', 'PG1')],
            [('New group', 'New sales', 'New goods', 'New brand', 'PG2', 'Supplier', 1.5)]]
        with patch('model_purch.services.goods_directory.connect_database', return_value=(connection, cursor)):
            self.page.goto(self.live_server_url + reverse('pggoods_list') + f'?scenario={self.source.pk}')
            self.page.screenshot(path='/tmp/goods-directory-toolbar.png', full_page=True)
            button = self.page.locator('#refreshGoodsDirectoryBtn')
            expect(button).to_be_enabled()
            volume = self.page.locator(f'tr[data-row-id="{self.good.pk}"] [name="volume"]')
            original = volume.input_value()
            volume.fill('2')
            expect(button).to_be_disabled()
            volume.fill(original)
            expect(button).to_be_enabled()
            with self.page.expect_navigation():
                button.click()
            expect(self.page.locator('.alert-success')).to_contain_text('Изменено: 1')
            expect(self.page.locator('.alert-success')).to_contain_text('Добавлено: 1')
            expect(self.page.locator('.alert-info')).to_contain_text('EXW = 0')
            expect(self.page.locator('.pricing-row')).to_have_count(2)
            row = self.page.locator(f'tr[data-row-id="{self.good.pk}"]')
            self.assertEqual(row.locator('[name="planning_sales"]').input_value(), 'New sales')
            self.assertEqual(row.locator('[name="group_goods"]').input_value(), 'New goods')
            self.assertEqual(row.locator('[name="brand"]').input_value(), 'New brand')
            self.assertEqual(row.locator('[name="purch"]').input_value(), 'Supplier')
            self.assertEqual(row.locator('[name="exw_usd"]').input_value(), '10.00')
            self.assertEqual(self.errors, [])
            connection.close.assert_called_once()

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
        copied = self.db(lambda: PGGoods.objects.get(scenario_plan=self.target))
        row = self.page.locator(f'tr[data-row-id="{copied.pk}"]')
        self.assertEqual(row.locator('[name="exw_usd"]').input_value(), '127.94')
        self.assertEqual(row.locator('[name="volume"]').input_value(), '0.23')
        self.assertEqual(row.locator('[name="container_volume"]').input_value(), '66.00')
        self.assertEqual(row.locator('[name="plan_volume"]').input_value(), '0.22')
        self.assertEqual(row.locator('[name="fact_volume"]').input_value(), '0.24')
        self.select(self.source)
        self.page.get_by_role('link', name='Закупки', exact=True).click()
        self.assertIn('77', self.page.locator(f'#purchase-{self.purchase.pk}').inner_text())
        self.assertEqual(self.errors, [], self.asset_events)

    def test_copy_goods_through_modal_and_preserve_selected_scenario(self):
        self.db(lambda: PGGoods.objects.filter(pk=self.good.pk).update(exw_usd=127.94, volume=.23, container_volume=66, plan_volume=.22, fact_volume=.24))
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
        self.assertIn('100%', self.page.locator(f'#purchase-{self.purchase.pk}').inner_text())
        self.assertIn('Payment', self.page.locator(f'#purchase-{self.purchase.pk}').inner_text())
        root = self.page.locator('#purchPaymentFilter').locator('xpath=following-sibling::div[1]')
        root.locator(':scope > button').click()
        text = self.page.locator('#purchPaymentFilter option[value="invalid"]').text_content()
        root.locator('input[type=search]').fill(text)
        root.locator('.searchable-filter-options button').get_by_text(text, exact=True).click()
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

    def test_product_duty_can_be_saved_from_goods_table(self):
        self.db(lambda: Freight.objects.filter(scenario=self.source).update(price_per_container=1000,customs_rate=22,warehouse_delivery_cost=500))
        self.db(lambda: PGGoods.objects.filter(pk=self.good.pk).update(exw_usd=100,volume=2,container_volume=50))
        self.visit('pggoods_list')
        self.select(self.source)
        field = self.page.locator('[data-duty-percent]')
        field.fill('10')
        row=self.page.locator(f'tr[data-row-id="{self.good.pk}"]')
        self.assertEqual(row.locator('[name="customs_payment_usd"]').input_value(),'33.88')
        self.assertEqual(row.locator('[name="ddp_usd"]').input_value(),'193.88')
        with self.page.expect_response(lambda response: '/pggoods/bulk-update/' in response.url) as result:
            self.page.locator('#saveAllBtn').click()
        self.assertEqual(result.value.status, 200)
        self.assertEqual(self.db(lambda: str(PGGoods.objects.get(pk=self.good.pk).duty_rate)), '0.1')
        self.assertAlmostEqual(self.db(lambda:PGGoods.objects.get(pk=self.good.pk).customs_payment_usd),33.88)
        self.page.reload()
        self.assertEqual(self.page.locator('[data-duty-percent]').input_value(), '10')
        self.page.locator('summary').filter(has_text='Как рассчитана стоимость').click()
        self.assertIn('193,88', self.page.locator('[data-price-explanation]').inner_text())
        self.page.screenshot(path='/tmp/goods-ux-desktop.png', full_page=True)
        field = self.page.locator('[data-duty-percent]')
        field.fill('15')
        self.page.locator('#discard-goods-changes').click()
        self.assertEqual(field.input_value(), '10')
        self.assertTrue(self.page.locator('#saveAllBtn').is_disabled())
        field.fill('20')
        field.press('Escape')
        self.assertEqual(field.input_value(), '10')
        self.assertTrue(row.locator('[name=brand]').is_visible())
        self.assertEqual(self.errors, [])

    def test_goods_source_check_explains_incoming_only_presence(self):
        from django.utils import timezone
        from playwright.sync_api import expect
        from .services.goods_presence import SOURCES
        rows = [{'key':key, 'label':value[0], 'note':value[1], 'found':key=='income', 'qualifies':key=='income',
                 'quantity':10 if key=='income' else None,'accounting_quantity':10 if key=='income' else None,
                 'amount':None,'ddp':None} for key,value in SOURCES.items()]
        with patch('model_purch.views_goods_presence.check_goods_presence',return_value={'rows':rows,'supported':True,'plans':['Plan'],'checked_at':timezone.now()}) as check:
            self.page.goto(self.live_server_url + reverse('pggoods_list') + f'?scenario={self.source.pk}')
            with self.page.expect_navigation():
                self.page.locator(f'tr[data-row-id="{self.good.pk}"]').get_by_role('link',name='Проверить источники').click()
            expect(self.page.get_by_role('heading',name='Почему товар присутствует в сценарии')).to_be_visible()
            expect(self.page.get_by_role('status')).to_contain_text('Есть данные в источниках')
            expect(self.page.locator('tbody tr').last).to_contain_text('Есть основание для включения')
            self.assertEqual(check.call_count,1)
            self.page.screenshot(path='/tmp/goods-presence-desktop.png',full_page=True)
            self.page.set_viewport_size({'width':390,'height':844})
            self.assertLessEqual(self.page.evaluate('document.documentElement.scrollWidth'),390)
            self.page.screenshot(path='/tmp/goods-presence-mobile.png',full_page=True)
            self.assertEqual(self.errors,[])

    def test_product_filters_order_search_and_two_decimal_price_display(self):
        from playwright.sync_api import expect
        self.db(lambda: PGGoods.objects.filter(pk=self.good.pk).update(exw_usd=1234.567, cif_usd=1234.567, stock_ddp_usd=987.654))
        self.visit('pggoods_list')
        self.select(self.source)
        names = self.page.locator('.filters-card [name]').evaluate_all('(fields) => fields.map(field => field.name).filter(name => name !== "scenario")')
        self.assertEqual(names, ['search', 'planning_group', 'planning_sales', 'group_goods', 'brand', 'purch', 'kind_purch', 'plan_warning', 'ddp_threshold'])
        row = self.page.locator(f'tr[data-row-id="{self.good.pk}"]')
        self.assertEqual(row.locator('[name="exw_usd"]').input_value(), '1234.57')
        for name in ('planning_sales', 'brand', 'kind_purch', 'plan_volume', 'fact_volume', 'plan_ddp_usd', 'fact_ddp_usd', 'stock_ddp_usd', 'kddp', 'freight_usd', 'cif_usd', 'customs_payment_usd', 'warehouse_delivery_usd', 'foreign_delivery_usd', 'nr_customs_vat_usd'):
            expect(row.locator(f'[name="{name}"]')).to_be_visible()
        expect(self.page.locator('#goods-all-fields, #toggle-ddp-details')).to_have_count(0)
        expect(row.locator('[name=stock_ddp_usd]')).to_have_value('987.65')
        self.assertTrue(row.locator('[name=stock_ddp_usd]').evaluate('(input) => input.readOnly'))
        self.assertEqual(row.locator('[name="cif_usd"]').input_value(), '1253.56')
        self.assertEqual(row.locator('select[name="planning_sales"]').count(), 1)
        self.assertEqual(row.locator('select[name="group_goods"]').count(), 1)
        self.assertEqual(row.locator('select[name="brand"]').count(), 1)
        root = self.page.locator('.filters-card select[name="planning_sales"]').locator('xpath=following-sibling::div[1]')
        root.locator(':scope > button').click()
        root.locator('input[type=search]').fill('sal')
        expect(root.locator('.searchable-filter-options button')).to_have_count(1)
        root.locator('.searchable-filter-options button').click()
        with self.page.expect_navigation():
            self.page.locator('.filters-card button[type=submit]').click()
        self.assertIn('planning_sales=Sales', self.page.url)
        expect(self.page.locator(f'tr[data-row-id="{self.good.pk}"]')).to_be_visible()
        self.assertEqual(self.errors, [])

    def test_purchase_analyst_can_view_and_download_without_edit_controls(self):
        from django.conf import settings
        from django.contrib.auth.models import User
        from base.models import UserProjectAccess
        from playwright.sync_api import expect
        def login():
            user=User.objects.create_user('browser-purchase-analyst')
            UserProjectAccess.objects.create(user=user,project='purchases',role='Аналитик')
            self.client.force_login(user)
        self.db(login)
        self.page.context.clear_cookies()
        self.page.context.add_cookies([{'name':settings.SESSION_COOKIE_NAME,'value':self.client.cookies[settings.SESSION_COOKIE_NAME].value,'url':self.live_server_url}])
        self.visit('pggoods_list');self.select(self.source)
        expect(self.page.get_by_role('status').filter(has_text='Аналитик')).to_be_visible()
        expect(self.page.locator('#saveAllBtn')).to_have_count(0)
        expect(self.page.locator('#refreshGoodsDirectoryForm')).to_have_count(0)
        expect(self.page.get_by_role('link',name='Импорт из Excel',exact=True)).not_to_be_visible()
        row=self.page.locator(f'tr[data-row-id="{self.good.pk}"]')
        expect(row.locator('[name=exw_usd]')).to_be_disabled()
        expect(row.locator('[name=planning_sales]')).to_be_disabled()
        expect(row.get_by_role('button',name='Удалить',exact=True)).not_to_be_visible()
        search=self.page.locator('input[name=search]')
        expect(search).to_be_enabled()
        search.fill('Group')
        with self.page.expect_navigation():search.press('Enter')
        expect(row).to_be_visible()
        with self.page.expect_download() as event:
            self.page.get_by_role('link',name='Экспорт в Excel',exact=True).click()
        self.assertTrue(event.value.suggested_filename.endswith('.xlsx'))
        self.page.screenshot(path='/tmp/purchase-analyst-goods.png',full_page=True)
        self.visit('scenario_list')
        expect(self.page.get_by_role('link',name='Создать сценарий',exact=True)).not_to_be_visible()
        expect(self.page.locator('#bulk-export-form')).not_to_be_visible()
        self.visit('goods_groups')
        expect(self.page.get_by_role('heading',name='Группы и пошлины',exact=True)).to_be_visible()
        expect(self.page.locator('.purchases-readonly form[method=post]')).to_have_count(0)
        self.page.goto(self.live_server_url+reverse('purch_edit',args=[self.purchase.pk]))
        expect(self.page.locator('[name=lag_income]')).to_be_disabled()
        expect(self.page.locator('#submitBtn')).not_to_be_visible()
        self.visit('results_page')
        expect(self.page.locator('button[onclick="startAlgorithm()"]').first).not_to_be_visible()
        expect(self.page.locator('button[onclick="startAlgorithm()"]').nth(1)).not_to_be_visible()
        self.assertEqual(self.errors,[])

    def test_fact_fields_and_weighted_ddp_follow_price_edits(self):
        self.db(lambda: PGGoods.objects.filter(pk=self.good.pk).update(
            plan_quantity=100, stock_quantity=25, stock_ddp_usd=80, fact_ddp_usd=90, fact_volume=.3))
        self.visit('pggoods_list')
        self.select(self.source)
        row = self.page.locator(f'tr[data-row-id="{self.good.pk}"]')
        self.assertEqual(row.locator('[name="fact_ddp_usd"]').input_value(), '90.00')
        self.assertEqual(row.locator('[name="fact_volume"]').input_value(), '0.30')
        row.locator('[name="exw_usd"]').fill('120')
        ddp = float(row.locator('[name="ddp_usd"]').input_value())
        weighted = float(row.locator('[name="weighted_ddp_usd"]').input_value())
        self.assertAlmostEqual(weighted, (ddp * 75 + 80 * 25) / 100, places=2)
        self.assertEqual(self.errors, [])

    def test_stock_above_plan_keeps_zero_purchase_and_positive_weighted_ddp(self):
        from playwright.sync_api import expect
        self.db(lambda: PGGoods.objects.filter(pk=self.good.pk).update(plan_quantity=100,stock_quantity=1000,stock_ddp_usd=80))
        self.visit('pggoods_list');self.select(self.source)
        row=self.page.locator(f'tr[data-row-id="{self.good.pk}"]')
        expect(row.locator('[data-purchase-plan-note]')).to_be_visible()
        expect(row.locator('[name="weighted_ddp_usd"]')).to_have_value('80.00')
        row.locator('[name="exw_usd"]').fill('2650.74')
        expect(row.locator('[name="weighted_ddp_usd"]')).to_have_value('80.00')
        self.assertEqual(self.errors,[])

    def test_common_purchase_settings_collapse_and_remember_state(self):
        from playwright.sync_api import expect
        self.visit('pggoods_list')
        self.select(self.source)
        self.page.locator('#freight-settings > summary').click()
        expect(self.page.locator('#freight-settings [name="price_per_container"]')).not_to_be_visible()
        expect(self.page.locator(f'tr[data-row-id="{self.good.pk}"]')).to_be_visible()
        self.page.reload()
        expect(self.page.locator('#freight-settings [name="price_per_container"]')).not_to_be_visible()
        self.page.locator('#freight-settings > summary').click()
        expect(self.page.locator('#freight-settings [name="price_per_container"]')).to_be_visible()
        self.assertEqual(self.errors, [])

    def test_group_default_and_price_preview_match_saved_price(self):
        from .models import GoodsGroup
        self.db(lambda: GoodsGroup.objects.create(name='Tariff group', duty_rate=.05))
        self.db(lambda: Freight.objects.filter(scenario=self.source).update(price_per_container=1000, customs_rate=10, warehouse_delivery_cost=500))
        self.visit('pggoods_list')
        self.select(self.source)
        row = self.page.locator(f'tr[data-row-id="{self.good.pk}"]')
        row.locator('[name="group_goods"]').select_option('Tariff group')
        self.assertEqual(row.locator('[data-duty-percent]').input_value(), '5')
        row.locator('[name="volume"]').fill('2')
        row.locator('[name="container_volume"]').fill('50')
        row.locator('[name="exw_usd"]').fill('100')
        self.assertEqual(row.locator('[name="ddp_usd"]').input_value(), '174.70')
        self.assertEqual(row.locator('[name="kddp"]').input_value(), '1.75')
        self.assertTrue(row.locator('[name="ddp_usd"]').get_attribute('readonly') is not None)
        self.assertTrue(row.locator('[name="percent_stock_end"]').get_attribute('readonly') is not None)
        with self.page.expect_response(lambda response: '/pggoods/bulk-update/' in response.url):
            self.page.locator('#saveAllBtn').click()
        self.assertAlmostEqual(self.db(lambda: PGGoods.objects.get(pk=self.good.pk).ddp_usd), 174.7)
        self.assertEqual(self.errors, [])
