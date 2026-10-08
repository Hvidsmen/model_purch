from unittest.mock import patch
from django.test import TestCase, override_settings
from django.urls import reverse
from .models import AlgorithmRun, AlgorithmStep, ScenarioModel, ScenarioExport, PGGoods, KindPurch
from .services.preflight import snapshot, fingerprint
from .services.calculation import run_step


@override_settings(MS_SQL_CONN_STR='test')
class BatchCalculationTests(TestCase):
    def setUp(self):
        self.scenarios = [ScenarioModel.objects.create(name=name, date_start_plan='2026-01-01', date_end_plan='2026-12-31')
                          for name in ['Plan A', 'Plan B']]
        kind = KindPurch.objects.create(name='Purchased')
        for scenario in self.scenarios:
            PGGoods.objects.create(scenario_plan=scenario, planning_group='Group', planning_sales='Sales', group_goods='Goods',
                kind_purch=kind, volume=1, exw_usd=10, ddp_usd=15, kddp=1.5, stock_cnt_day=30, percent_stock_end=20)
            self.export(scenario)

    def export(self, scenario):
        parameters = snapshot(scenario)
        return ScenarioExport.objects.create(scenario=scenario, parameters=parameters, fingerprint=fingerprint(parameters))

    def start(self):
        return self.client.post(reverse('start_algorithm_api'), {'scenario': self.scenarios[0].pk})

    def test_selected_scenario_does_not_limit_calculation(self):
        self.client.get(reverse('purch_list'), {'scenario': self.scenarios[0].pk})
        response = self.start()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.json()['scenario_ids']), {scenario.pk for scenario in self.scenarios})
        run = AlgorithmRun.objects.get()
        with patch('model_purch.calc_purch.calc_purch') as calculate:
            run_step(3, run.pk, None)
            calculate.assert_called_once_with()

    def test_stale_unselected_scenario_blocks_entire_batch(self):
        PGGoods.objects.filter(scenario_plan=self.scenarios[1]).update(exw_usd=12)
        response = self.start()
        self.assertEqual(response.status_code, 400)
        self.assertIn('Plan B', response.json()['error'])
        self.assertIn('Повторите экспорт', response.json()['error'])
        self.assertFalse(AlgorithmRun.objects.exists())

    def test_changed_unselected_scenario_blocks_next_step(self):
        run_id = self.start().json()['run_id']
        PGGoods.objects.filter(scenario_plan=self.scenarios[1]).update(stock_cnt_day=50)
        with patch('model_purch.views._run_algorithm_step') as execute:
            self.assertEqual(self.client.post(reverse('execute_next_step_api', args=[run_id])).status_code, 409)
            execute.assert_not_called()
        self.assertEqual(AlgorithmRun.objects.get(pk=run_id).status, 'failed')

    def test_added_or_deleted_scenario_blocks_next_step(self):
        for action in ['add', 'delete']:
            with self.subTest(action=action):
                run_id = self.start().json()['run_id']
                if action == 'add':
                    added = ScenarioModel.objects.create(name='New', date_start_plan='2026-01-01', date_end_plan='2026-12-31')
                else:
                    self.scenarios[1].delete()
                self.assertEqual(self.client.post(reverse('execute_next_step_api', args=[run_id])).status_code, 409)
                if action == 'add':
                    added.delete()

    def test_results_show_every_scenario_even_when_one_is_selected(self):
        response = self.client.get(reverse('results_page'), {'scenario': self.scenarios[0].pk})
        self.assertContains(response, 'Область расчёта: <strong>Все сценарии</strong>')
        self.assertEqual(len(response.context['scenario_checks']), 2)
        self.assertEqual(response.context['readiness_errors'], [])
