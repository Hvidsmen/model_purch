from unittest.mock import patch
from django.test import TestCase
from django.urls import reverse
from base.testing import authorize_test_case
from .models import ScenarioModel, KindPurch, PGGoods, Freight, ScenarioPlanSales, ScenarioExport, AlgorithmRun
from .services.preflight import batch_readiness, snapshot, fingerprint
from .services.calculation import run_step


class ScenarioArchiveTests(TestCase):
    def setUp(self):
        authorize_test_case(self)
        self.scenario = ScenarioModel.objects.create(name='Archived', date_start_plan='2026-01-01', date_end_plan='2026-12-31')
        self.active = ScenarioModel.objects.create(name='Active', date_start_plan='2027-01-01', date_end_plan='2027-12-31')
        kind = KindPurch.objects.create(name='Purchased')
        self.good = PGGoods.objects.create(scenario_plan=self.scenario, planning_group='Group', planning_sales='Sales',
            group_goods='Goods', kind_purch=kind, volume=.23, exw_usd=127.94, stock_cnt_day=30, percent_stock_end=0,
            plan_volume=.22, fact_volume=.24, plan_quantity=100, stock_quantity=25, stock_ddp_usd=80)
        self.freight = Freight.objects.create(scenario=self.scenario, price_per_container=100)
        self.plan = ScenarioPlanSales.objects.create(scenario_model=self.scenario, name='Plan', flag_order_in_purch=True)
        parameters = snapshot(self.scenario)
        self.export = ScenarioExport.objects.create(scenario=self.scenario, parameters=parameters, fingerprint=fingerprint(parameters))

    def test_archive_retains_data_and_restore_returns_same_scenario(self):
        original = PGGoods.objects.filter(pk=self.good.pk).values().get()
        response = self.client.post(reverse('scenario_delete', args=[self.scenario.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertFalse(ScenarioModel.objects.filter(pk=self.scenario.pk).exists())
        self.assertIsNotNone(ScenarioModel.all_objects.get(pk=self.scenario.pk).archived_at)
        self.assertEqual(PGGoods.objects.filter(pk=self.good.pk).values().get(), original)
        self.assertTrue(Freight.objects.filter(pk=self.freight.pk).exists())
        self.assertTrue(ScenarioPlanSales.objects.filter(pk=self.plan.pk).exists())
        self.assertTrue(ScenarioExport.objects.filter(pk=self.export.pk).exists())
        _, _, checks = batch_readiness()
        self.assertEqual([check['scenario'].pk for check in checks], [self.active.pk])
        page = self.client.get(reverse('pggoods_list'))
        self.assertEqual([s.pk for s in page.context['scenarios']], [self.active.pk])
        self.assertEqual(self.client.get(reverse('scenario_edit', args=[self.scenario.pk])).status_code, 404)
        self.assertContains(self.client.get(reverse('scenario_archive')), 'Archived')
        self.assertEqual(self.client.get(reverse('scenario_restore', args=[self.scenario.pk])).status_code, 405)
        self.client.post(reverse('scenario_restore', args=[self.scenario.pk]))
        self.assertTrue(ScenarioModel.objects.filter(pk=self.scenario.pk).exists())
        self.assertEqual(PGGoods.objects.filter(pk=self.good.pk).values().get(), original)

    def test_batch_calculation_uses_only_recorded_active_names(self):
        self.client.post(reverse('scenario_delete', args=[self.scenario.pk]))
        _, parameters, _ = batch_readiness()
        run = AlgorithmRun.objects.create(parameters=parameters)
        with patch('model_purch.calc_purch.calc_purch') as calculate:
            run_step(3, run.pk, None)
        calculate.assert_called_once_with(scenario_names=['Active'])
