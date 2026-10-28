from decimal import Decimal
from django.test import TestCase
from django.urls import reverse
from base.testing import authorize_admin
from .models import PGGoods, Purch, Freight, ScenarioModel, KindPurch, GoodsGroup
from .services.pricing import apply_price, reprice_goods


class ProductPricingTests(TestCase):
    def test_plan_validation_threshold_missing_values_and_filters(self):
        from .services.plan_validation import plan_warnings
        self.good.plan_ddp_usd = 100
        self.good.ddp_usd = 110
        self.good.plan_volume = self.good.volume
        self.assertEqual(plan_warnings(self.good), [])
        self.good.ddp_usd = 111
        self.assertEqual([kind for kind, _ in plan_warnings(self.good)], ['ddp'])
        self.assertEqual(plan_warnings(self.good, 12), [])
        self.good.plan_volume = 1
        self.good.volume = 1.1
        self.assertEqual(plan_warnings(self.good, 12), [])
        self.assertEqual([kind for kind, _ in plan_warnings(self.good)], ['ddp'])
        self.good.volume = 1.11
        self.assertEqual([kind for kind, _ in plan_warnings(self.good)], ['ddp', 'volume'])
        self.assertEqual(plan_warnings(self.good, 12), [])
        self.good.volume = .89
        self.assertEqual([kind for kind, _ in plan_warnings(self.good)], ['ddp', 'volume'])
        self.good.plan_volume = 0
        self.assertEqual([kind for kind, _ in plan_warnings(self.good)], ['ddp', 'volume'])
        self.good.plan_volume = None
        self.assertEqual([kind for kind, _ in plan_warnings(self.good)], ['ddp', 'missing'])
        self.good.plan_ddp_usd = 0
        self.assertEqual([kind for kind, _ in plan_warnings(self.good)], ['ddp', 'missing'])
        PGGoods.objects.filter(pk=self.good.pk).update(plan_ddp_usd=1, plan_volume=3)
        url = reverse('pggoods_list')
        for kind in ['ddp', 'volume', 'any']:
            response = self.client.get(url, {'scenario': self.scenario.pk, 'plan_warning': kind})
            self.assertEqual([good.pk for good in response.context['page_obj']], [self.good.pk])
        response = self.client.get(url, {'scenario': self.scenario.pk, 'plan_warning': 'missing'})
        self.assertEqual(list(response.context['page_obj']), [])
        PGGoods.objects.filter(pk=self.good.pk).update(plan_ddp_usd=None, plan_volume=None)
        response = self.client.get(url, {'scenario': self.scenario.pk, 'plan_warning': 'missing', 'ddp_threshold': 'NaN'})
        self.assertEqual(response.context['plan_threshold'], 10)
        self.assertContains(response, 'Не загружены плановые значения')

    def test_fact_discrepancy_warnings_use_threshold_and_separate_filters(self):
        from .services.plan_validation import plan_warnings
        self.good.plan_ddp_usd = self.good.ddp_usd
        self.good.plan_volume = self.good.volume
        self.good.fact_ddp_usd = self.good.ddp_usd / 1.1
        self.good.fact_volume = self.good.volume / 1.1
        self.assertEqual(plan_warnings(self.good), [])
        self.good.fact_ddp_usd = self.good.ddp_usd / 1.11
        self.good.fact_volume = self.good.volume / 1.11
        self.assertEqual([kind for kind, _ in plan_warnings(self.good)], ['fact_ddp', 'fact_volume'])
        self.assertEqual(plan_warnings(self.good, 12), [])
        PGGoods.objects.filter(pk=self.good.pk).update(fact_ddp_usd=self.good.fact_ddp_usd, fact_volume=self.good.fact_volume)
        url = reverse('pggoods_list')
        for kind in ['fact_ddp', 'fact_volume']:
            page = self.client.get(url, {'scenario': self.scenario.pk, 'plan_warning': kind})
            self.assertEqual([good.pk for good in page.context['page_obj']], [self.good.pk])
            self.assertContains(page, 'отличается от факта')
        self.good.fact_ddp_usd = self.good.fact_volume = None
        self.assertEqual(plan_warnings(self.good), [])
        self.good.fact_ddp_usd = self.good.fact_volume = 0
        self.assertEqual([kind for kind, _ in plan_warnings(self.good)], ['fact_ddp', 'fact_volume'])

    def test_stock_covering_plan_has_zero_purchase_and_stock_ddp(self):
        self.good.plan_quantity=100
        self.good.ddp_usd=2650.74
        self.good.stock_ddp_usd=1000
        for stock in (100,200,1000):
            with self.subTest(stock=stock):
                self.good.stock_quantity=stock
                self.assertEqual(self.good.purchase_plan_quantity,0)
                self.assertEqual(self.good.weighted_ddp_usd,1000)
        self.good.stock_ddp_usd=0
        self.assertEqual(self.good.weighted_ddp_usd,0)
        self.good.stock_ddp_usd=None
        self.assertIsNone(self.good.weighted_ddp_usd)
        self.good.stock_ddp_usd=-10
        self.assertIsNone(self.good.weighted_ddp_usd)
        self.good.stock_quantity=-1
        self.assertIsNone(self.good.weighted_ddp_usd)

    def test_fact_stock_parameters_and_weighted_ddp(self):
        from unittest.mock import Mock
        from .services.erp_plan_parameters import update_parameters
        from django.core.exceptions import ValidationError
        self.scenario.refresh_from_db()
        connection, cursor = Mock(), Mock()
        cursor.fetchall.return_value = [('Unit', 200, .5, 100, 80, .4, 25, 90, .3, 200)]
        update_parameters(self.scenario, ['Plan'], Mock(return_value=(connection, cursor)))
        self.good.refresh_from_db()
        self.assertEqual((self.good.fact_ddp_usd, self.good.fact_volume, self.good.fact_quantity), (90, .3, 200))
        self.assertEqual((self.good.plan_quantity, self.good.stock_quantity, self.good.stock_ddp_usd), (100, 25, 80))
        self.good.ddp_usd = 120
        self.assertEqual(self.good.weighted_ddp_usd, 110)
        self.good.stock_quantity = None
        self.assertEqual(self.good.weighted_ddp_usd, 120)
        self.good.plan_quantity = 0
        self.assertIsNone(self.good.weighted_ddp_usd)
        page = self.client.get(reverse('pggoods_list'), {'scenario': self.scenario.pk})
        for name in ['fact_volume', 'fact_ddp_usd', 'weighted_ddp_usd']:
            self.assertContains(page, f'name="{name}"')
        cursor.fetchall.return_value = [('Unit', 200, .5, 100, 80, .4, -1, 90, .3, 200)]
        with self.assertRaises(ValidationError):
            update_parameters(self.scenario, ['Plan'], Mock(return_value=(connection, cursor)))
        self.good.refresh_from_db()
        self.assertEqual(self.good.stock_quantity, 25)
        cursor.fetchall.return_value = [('Other', 200, .5, 100, None, None, None, None, None, None)]
        update_parameters(self.scenario, ['Plan'], Mock(return_value=(connection, cursor)))
        self.good.refresh_from_db()
        self.assertIsNone(self.good.fact_ddp_usd)
        self.assertIsNone(self.good.stock_quantity)

    def test_erp_stock_fallback_merges_volumes_by_stock_quantity(self):
        from unittest.mock import Mock
        from .services.erp_plan_parameters import update_parameters, SQL
        self.scenario.refresh_from_db()
        connection, cursor = Mock(), Mock()
        # Stock-only groups retain fallback plan parameters without inventing plan quantity.
        cursor.fetchall.return_value = [('Unit', 100, .1, None, 100, .1, 10, None, None, None), ('Unit', 300, .5, None, 300, .5, 30, None, None, None)]
        update_parameters(self.scenario, ['Plan'], Mock(return_value=(connection, cursor)))
        self.good.refresh_from_db()
        self.assertEqual(self.good.plan_ddp_usd, 250)
        self.assertEqual(self.good.plan_volume, .4)
        self.assertIsNone(self.good.plan_quantity)
        self.assertEqual(self.good.stock_quantity, 40)
        self.assertIsNone(self.good.weighted_ddp_usd)
        self.assertIn('t_Даичи_ПервичнаяСебестоимостьOnDate', SQL)
        self.assertIn('LEFT JOIN sales', SQL)
        self.assertIn('SUM(s.DDP_USD) AS DDPStock', SQL)

    def test_erp_duplicate_group_variants_merge_weighted_by_quantity(self):
        from unittest.mock import Mock
        from .services.erp_plan_parameters import update_parameters
        self.scenario.refresh_from_db()
        connection, cursor = Mock(), Mock()
        cursor.fetchall.return_value = [('Unit', 100, .1, 10, None, None, None, None, None, None), (' unit ', 300, .5, 30, None, None, None, None, None, None)]
        self.assertEqual(update_parameters(self.scenario, ['Plan'], Mock(return_value=(connection, cursor))), (1, 0))
        self.good.refresh_from_db()
        self.assertEqual(self.good.plan_ddp_usd, 250)
        self.assertEqual(self.good.plan_volume, .4)
        cursor.fetchall.return_value = [('Unit', None, None, 10, None, None, None, None, None, None), ('UNIT', None, None, 30, None, None, None, None, None, None)]
        update_parameters(self.scenario, ['Plan'], Mock(return_value=(connection, cursor)))
        self.good.refresh_from_db()
        self.assertIsNone(self.good.plan_ddp_usd)
        self.assertIsNone(self.good.plan_volume)

    def test_erp_parameters_use_scenario_plans_and_reject_changes_during_load(self):
        from unittest.mock import Mock
        from .models import ScenarioPlanSales
        from .services.erp_plan_parameters import update_parameters
        from django.core.exceptions import ValidationError
        self.scenario.refresh_from_db()
        connector = Mock()
        with self.assertRaises(ValidationError):
            update_parameters(self.scenario, connector=connector)
        connector.assert_not_called()
        first = ScenarioPlanSales.objects.create(scenario_model=self.scenario, name=' План 2027 ', flag_order_in_purch=True)
        ScenarioPlanSales.objects.create(scenario_model=self.scenario, name='План 2026', flag_order_in_purch=False)
        connection, cursor = Mock(), Mock()
        connector.return_value = (connection, cursor)
        cursor.fetchall.return_value = [('Unit', 200, .5, 1, None, None, None, None, None, None)]
        update_parameters(self.scenario, connector=connector)
        self.assertEqual(cursor.execute.call_args.args[-2:], ('План 2027', 'План 2026'))
        self.good.refresh_from_db()
        self.assertEqual(self.good.plan_ddp_usd, 200)
        page = self.client.get(reverse('goods_plan_parameters', args=[self.scenario.pk]))
        self.assertContains(page, 'План 2027')
        self.assertNotContains(page, 'name="sources"')
        def change_plans():
            first.name = 'Изменённый план'
            first.save()
            return [('Unit', 999, .9, 1, None, None, None, None, None, None)]
        cursor.fetchall.side_effect = change_plans
        with self.assertRaises(ValidationError):
            update_parameters(self.scenario, connector=connector)
        self.good.refresh_from_db()
        self.assertEqual(self.good.plan_ddp_usd, 200)

    def test_erp_plan_parameters_match_group_preserve_prices_and_use_bound_filters(self):
        from unittest.mock import Mock, patch
        from .services.erp_plan_parameters import update_parameters, SQL
        connection, cursor = Mock(), Mock()
        cursor.fetchall.return_value = [(' unit ', 123.456789, .987654321, 1, None, None, None, None, None, None)]
        connector = Mock(return_value=(connection, cursor))
        self.scenario.refresh_from_db()
        original = (self.good.ddp_usd, self.good.volume)
        matched, missing = update_parameters(self.scenario, ["Plan ' one", 'Plan two'], connector)
        self.assertEqual((matched, missing), (1, 0))
        self.good.refresh_from_db()
        self.assertEqual((self.good.plan_ddp_usd, self.good.plan_volume), (123.456789, .987654321))
        self.assertEqual((self.good.ddp_usd, self.good.volume), original)
        self.scenario.refresh_from_db()
        self.assertEqual(self.scenario.erp_sales_plan_sources, ["Plan ' one", 'Plan two'])
        args = cursor.execute.call_args.args
        self.assertEqual(args[1:], (str(self.scenario.date_start_plan), str(self.scenario.date_end_plan), "Plan ' one", 'Plan two'))
        self.assertIn('d.FullDateAlternateKey BETWEEN (SELECT DateFrom FROM parameters) AND (SELECT DateTo FROM parameters)', SQL)
        self.assertNotIn("Plan ' one", args[0])
        self.assertIn('HAVING SUM(p.Количество * d.P_ * mce.FlagAccountingQtyBA) > 0', SQL)
        self.assertNotIn('DROP', SQL)
        cursor.fetchall.return_value = []
        from django.core.exceptions import ValidationError
        with self.assertRaises(ValidationError):
            update_parameters(self.scenario, ['Plan'], connector)
        self.good.refresh_from_db()
        self.assertEqual(self.good.plan_ddp_usd, 123.456789)
        url = reverse('goods_plan_parameters', args=[self.scenario.pk])
        self.assertContains(self.client.get(url), 'Планы продаж текущего сценария')
        page = self.client.get(reverse('pggoods_list'), {'scenario': self.scenario.pk})
        self.assertContains(page, 'name="plan_ddp_usd"')
        self.assertContains(page, 'name="plan_volume"')
        with patch('model_purch.views_plan_parameters.update_parameters', return_value=(1, 0)) as update:
            self.client.post(url, {'action': 'load', 'sources': ['Plan']})
            update.assert_not_called()
            response = self.client.post(url, {'action': 'load', 'confirm': 'yes', 'sources': ['Plan']})
            self.assertEqual(response.status_code, 302)
            update.assert_called_once()

    def test_duty_accepts_float_precision_in_product_and_group(self):
        value = .07123456789123
        response = self.client.post(reverse('edit_pggoods', args=[self.good.pk]), {'scenario': self.scenario.pk, 'duty_rate': str(value)})
        self.assertEqual(response.status_code, 302)
        self.good.refresh_from_db()
        self.assertEqual(self.good.duty_rate, value)
        self.assertAlmostEqual(self.good.customs_payment_usd, 140 * .1 * (1 + value))
        response = self.client.post(reverse('goods_groups'), {'name': 'Precise duty', 'duty_rate': str(value), 'container_volume': '65'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(GoodsGroup.objects.get(name='Precise duty').duty_rate, value)

    def setUp(self):
        authorize_admin(self.client)
        self.scenario = ScenarioModel.objects.create(name='Costs', date_start_plan='2026-01-01', date_end_plan='2026-12-31')
        self.kind = KindPurch.objects.create(name='Imported')
        self.supplier = Purch.objects.create(name='Supplier', lag_income=30)
        self.freight = Freight.objects.create(scenario=self.scenario, price_per_container=1000, customs_rate=10, warehouse_delivery_cost=500)
        self.good = PGGoods.objects.create(scenario_plan=self.scenario, planning_group='Unit', planning_sales='Sales',
            group_goods='Group', brand='Brand', purch='Supplier', kind_purch=self.kind, volume=2, container_volume=50,
            duty_rate=.05, exw_usd=100, stock_cnt_day=30, percent_stock_end=20)

    def test_duty_is_a_coefficient_and_is_not_divided_by_100(self):
        self.good.duty_rate=.1
        self.good.save()
        self.good.refresh_from_db()
        self.assertEqual(self.good.duty_rate,.1)
        self.assertAlmostEqual(self.good.customs_payment_usd,15.4) # 140 × 10% × 1.1.
        self.assertAlmostEqual(self.good.ddp_usd,175.4)
        self.freight.price_per_container=0
        self.freight.customs_rate=22
        self.freight.warehouse_delivery_cost=0
        self.good.exw_usd=68710.91
        apply_price(self.good,self.freight)
        self.assertAlmostEqual(self.good.customs_payment_usd,16628.04022)

    def test_formula_percentages_and_domestic_exception(self):
        self.assertAlmostEqual(self.good.freight_usd, 40)
        self.assertAlmostEqual(self.good.cif_usd, 140)
        self.assertAlmostEqual(self.good.customs_payment_usd, 14.7)
        self.assertAlmostEqual(self.good.warehouse_delivery_usd, 20)
        self.assertAlmostEqual(self.good.ddp_usd, 174.7)
        self.assertAlmostEqual(self.good.kddp, 1.747)
        self.supplier.supply_type = 'russian'
        self.supplier.save()
        self.good.save()
        self.assertEqual((self.good.ddp_usd, self.good.kddp), (100, 1))
        self.good.exw_usd = 0
        self.good.kddp = 1.5
        apply_price(self.good, self.freight, False)
        self.assertAlmostEqual(self.good.ddp_usd, 64.2)
        self.assertEqual(self.good.kddp, 1.5)

    def test_user_cannot_change_calculated_values_or_stock_percentage(self):
        data = {'id': self.good.pk, 'ddp_usd': 99999, 'kddp': 999, 'freight_usd': 999,
                'cif_usd': 999, 'customs_payment_usd': 999, 'warehouse_delivery_usd': 999,
                'percent_stock_end': 99, 'planning_group': 'Other', 'planning_sales': 'Changed sales',
                'brand': 'Changed brand', 'exw_usd': 200}
        self.client.post(reverse('bulk_update_pggoods') + f'?scenario={self.scenario.pk}', {'updates': [data]}, content_type='application/json')
        self.good.refresh_from_db()
        self.assertEqual((self.good.planning_group, self.good.percent_stock_end), ('Unit', 20))
        self.assertEqual((self.good.planning_sales, self.good.brand), ('Changed sales', 'Changed brand'))
        self.assertAlmostEqual(self.good.ddp_usd, 285.2)
        self.assertAlmostEqual(self.good.kddp, 1.426)
        response = self.client.get(reverse('edit_pggoods', args=[self.good.pk]) + f'?scenario={self.scenario.pk}')
        self.assertEqual(set(response.context['form'].fields), {'purch', 'planning_sales', 'group_goods', 'brand', 'kind_purch', 'volume', 'container_volume', 'duty_rate', 'exw_usd', 'stock_cnt_day'})

    def test_group_duty_default_and_explicit_override(self):
        group = GoodsGroup.objects.create(name='New group', duty_rate=.12)
        url = reverse('edit_pggoods', args=[self.good.pk]) + f'?scenario={self.scenario.pk}'
        self.client.post(url, {'scenario': self.scenario.pk, 'group_goods': group.name})
        self.good.refresh_from_db()
        self.assertEqual(self.good.duty_rate, .12)
        self.client.post(url, {'scenario': self.scenario.pk, 'duty_rate': '0.0725'})
        self.good.refresh_from_db()
        self.assertEqual(self.good.duty_rate, .0725)
        new = PGGoods.objects.create(scenario_plan=self.scenario, planning_group='New', planning_sales='Sales', group_goods=group.name,
            kind_purch=self.kind, volume=2, exw_usd=100, stock_cnt_day=1, percent_stock_end=0)
        self.assertEqual(new.duty_rate, .12)

    def test_scenario_parameters_reprice_existing_goods(self):
        response = self.client.post(reverse('freight'), {'scenario': self.scenario.pk, 'price_per_container': '2000',
            'customs_rate': '20', 'warehouse_delivery_cost': '1000'})
        self.assertEqual(response.status_code, 302)
        self.good.refresh_from_db()
        self.assertAlmostEqual(self.good.ddp_usd, 257.8)
        self.assertEqual(self.good.percent_stock_end, 20)

    def test_supplier_flag_edit_reprices_all_scenarios(self):
        data = {'name': self.supplier.name, 'lag_income': 30, 'lage_make': 0, 'supply_type': 'russian',
            'purchpay_set-TOTAL_FORMS': 0, 'purchpay_set-INITIAL_FORMS': 0,
            'purchpay_set-MIN_NUM_FORMS': 0, 'purchpay_set-MAX_NUM_FORMS': 10}
        response = self.client.post(reverse('purch_edit', args=[self.supplier.pk]), data)
        self.assertEqual(response.status_code, 302)
        self.good.refresh_from_db()
        self.assertEqual((self.good.ddp_usd, self.good.kddp), (100, 1))

    def test_group_directory_can_be_edited_by_logistician(self):
        from base.access import LOGISTICIAN
        from django.contrib.auth.models import Group, User
        user = User.objects.create_user('logistician')
        user.groups.add(Group.objects.get(name=LOGISTICIAN))
        self.client.force_login(user)
        response = self.client.post(reverse('goods_groups'), {'name': 'Duty group', 'duty_rate': '0.0725', 'container_volume': '65'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(GoodsGroup.objects.get(name='Duty group').duty_rate, .0725)

    def test_copy_recalculates_prices_using_target_scenario(self):
        from .services.copying import copy_goods
        target = ScenarioModel.objects.create(name='Target costs', date_start_plan='2027-01-01', date_end_plan='2027-12-31')
        Freight.objects.create(scenario=target, price_per_container=2000, customs_rate=20, warehouse_delivery_cost=1000)
        copy_goods(self.scenario, target)
        copied = PGGoods.objects.get(scenario_plan=target)
        self.assertAlmostEqual(copied.ddp_usd, 257.8)
        self.assertAlmostEqual(copied.kddp, 2.578)
        self.assertAlmostEqual(self.good.ddp_usd, 174.7)

    def test_bulk_response_returns_server_prices_and_only_saved_rows(self):
        other = PGGoods.objects.create(scenario_plan=self.scenario, planning_group='Other', planning_sales='Sales',
            group_goods='Group', kind_purch=self.kind, volume=1, exw_usd=100, stock_cnt_day=1, percent_stock_end=0)
        response = self.client.post(reverse('bulk_update_pggoods') + f'?scenario={self.scenario.pk}',
            {'updates': [{'id': self.good.pk, 'exw_usd': 200}, {'id': other.pk, 'volume': 0}]}, content_type='application/json')
        self.assertEqual([row['id'] for row in response.json()['rows']], [self.good.pk])
        self.assertAlmostEqual(response.json()['rows'][0]['values']['ddp_usd'], 285.2)
        self.assertEqual(len(response.json()['errors']), 1)

    def test_apply_group_duty_replaces_overrides_across_scenarios_and_reprices(self):
        group = GoodsGroup.objects.get(name='Group')
        group.duty_rate = Decimal('0.2')
        group.save()
        other_scenario = ScenarioModel.objects.create(name='Other', date_start_plan='2027-01-01', date_end_plan='2027-12-31')
        second = PGGoods.objects.create(scenario_plan=other_scenario, planning_group='Second',
            group_goods=' group ', kind_purch=self.kind, volume=1, container_volume=65, duty_rate=.03, exw_usd=50, stock_cnt_day=30, percent_stock_end=0)
        unrelated = PGGoods.objects.create(scenario_plan=self.scenario, planning_group='Unrelated',
            group_goods='Different', kind_purch=self.kind, volume=1, container_volume=65, duty_rate=.07, exw_usd=80, stock_cnt_day=30, percent_stock_end=0)
        self.good.refresh_from_db()
        self.assertEqual(self.good.duty_rate, .05)
        response = self.client.post(reverse('goods_groups'), {'id': group.pk, 'action': 'apply_duty', 'confirm_apply': 'yes'})
        self.assertEqual(response.status_code, 302)
        self.good.refresh_from_db(); second.refresh_from_db(); unrelated.refresh_from_db()
        self.assertEqual(self.good.duty_rate, .2)
        self.assertEqual(second.duty_rate, .2)
        self.assertEqual(unrelated.duty_rate, .07)
        self.assertAlmostEqual(self.good.customs_payment_usd, 16.8)
        self.assertAlmostEqual(self.good.ddp_usd, 176.8)
        self.assertAlmostEqual(self.good.kddp, 1.768)

    def test_apply_group_duty_requires_confirmation_and_rolls_back_invalid_prices(self):
        group = GoodsGroup.objects.get(name='Group')
        group.duty_rate = Decimal('0.2')
        group.save()
        self.client.post(reverse('goods_groups'), {'id': group.pk, 'action': 'apply_duty'})
        self.good.refresh_from_db()
        self.assertEqual(self.good.duty_rate, .05)
        PGGoods.objects.filter(pk=self.good.pk).update(container_volume=0)
        self.client.post(reverse('goods_groups'), {'id': group.pk, 'action': 'apply_duty', 'confirm_apply': 'yes'})
        self.good.refresh_from_db()
        self.assertEqual(self.good.duty_rate, .05)
        self.assertAlmostEqual(self.good.ddp_usd, 174.7)

    def test_product_classification_fields_offer_existing_and_reference_choices(self):
        from PlanningSystem.models import PlanningGroupSalesRef, GroupERPRef, BrangRef
        from .forms import PGGoodsEditForm
        from django.forms import Select
        PlanningGroupSalesRef.objects.create(planning_group_sales_name='Reference sales')
        GroupERPRef.objects.create(group_ERP_name='Reference group')
        BrangRef.objects.create(brand_name='Reference brand')
        form = PGGoodsEditForm(instance=self.good)
        for field, value in [('planning_sales', 'Reference sales'), ('group_goods', 'Reference group'), ('brand', 'Reference brand')]:
            self.assertIsInstance(form.fields[field].widget, Select)
            self.assertIn(value, dict(form.fields[field].widget.choices))

    def test_p2_expenses_and_vat_are_separate_from_ddp(self):
        self.assertEqual(self.supplier.supply_type, 'p2')
        self.assertEqual(self.freight.foreign_delivery_cost, 1100)
        self.assertEqual(self.freight.nr_customs_vat_rate, 10)
        self.good.save()
        self.assertAlmostEqual(self.good.foreign_delivery_usd, 44)
        self.assertAlmostEqual(self.good.nr_customs_vat_usd, 1.47)
        self.assertAlmostEqual(self.good.ddp_usd, 174.7)
        self.supplier.supply_type = 'direct'
        self.supplier.save()
        self.good.save()
        self.assertEqual(self.good.nr_customs_vat_usd, 0)
        self.assertAlmostEqual(self.good.foreign_delivery_usd, 44)
        self.assertAlmostEqual(self.good.ddp_usd, 174.7)
        self.supplier.supply_type = 'russian'
        self.supplier.save()
        self.good.save()
        self.assertEqual(self.good.nr_customs_vat_usd, 0)
        self.assertEqual(self.good.ddp_usd, self.good.exw_usd)
        for field in ('freight_usd', 'cif_usd', 'customs_payment_usd', 'warehouse_delivery_usd', 'foreign_delivery_usd', 'nr_customs_vat_usd'):
            self.assertEqual(getattr(self.good, field), 0)

    def test_group_container_and_bulk_parameters(self):
        group = GoodsGroup.objects.get(name='Group')
        self.assertEqual(group.container_volume, 65)
        group.container_volume = 100
        group.duty_rate = .2
        group.save()
        response = self.client.post(reverse('goods_groups'), {
            'action': 'apply_parameters', 'confirm_apply': 'yes',
            'group_ids': [group.pk], 'parameter': 'container_volume',
        })
        self.assertEqual(response.status_code, 302)
        self.good.refresh_from_db()
        self.assertEqual(self.good.container_volume, 100)
        self.assertEqual(self.good.duty_rate, .05)
        self.assertAlmostEqual(self.good.freight_usd, 20)
        self.client.post(reverse('goods_groups'), {
            'action': 'apply_parameters', 'confirm_apply': 'yes',
            'group_ids': [group.pk], 'parameter': 'both',
        })
        self.good.refresh_from_db()
        self.assertEqual(self.good.duty_rate, .2)
        self.assertAlmostEqual(self.good.customs_payment_usd, 14.4)

    def test_group_container_application_requires_confirmation(self):
        group = GoodsGroup.objects.get(name='Group')
        self.client.post(reverse('goods_groups'), {
            'action': 'apply_parameters', 'group_ids': [group.pk], 'parameter': 'both',
        })
        self.good.refresh_from_db()
        self.assertEqual(self.good.container_volume, 50)
