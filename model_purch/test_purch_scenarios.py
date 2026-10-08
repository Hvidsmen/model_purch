from base.testing import authorize_test_case
from unittest.mock import Mock, patch
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.urls import reverse
from .models import ScenarioModel, Purch, PurchPay, PGGoods, KindPurch, ScenarioExport
from .services.preflight import snapshot, fingerprint, calculation_readiness
from .services.purchases import coverage_errors


@override_settings(MS_SQL_CONN_STR='test')
class SharedPurchaseTests(TestCase):
    def setUp(self):
        authorize_test_case(self)
        self.scenarios = [ScenarioModel.objects.create(name=name, date_start_plan='2026-01-01', date_end_plan='2026-12-31')
                          for name in ['A', 'B']]
        self.purchase = Purch.objects.create(name='Supplier', lag_income=90, lage_make=35)
        PurchPay.objects.create(purch=self.purchase, name='Payment', percent_pay=100, lag_day_pay=10)
        kind = KindPurch.objects.create(name='Purchased')
        for scenario in self.scenarios:
            PGGoods.objects.create(scenario_plan=scenario, planning_group='Group', planning_sales='Sales',
                group_goods='Goods', purch='Supplier', kind_purch=kind, volume=1, exw_usd=2, ddp_usd=3,
                kddp=1.5, stock_cnt_day=30, percent_stock_end=20)

    def form_data(self, name='Supplier'):
        return {'name': name, 'lag_income': 77, 'lage_make': 35,
                'purchpay_set-TOTAL_FORMS': 1, 'purchpay_set-INITIAL_FORMS': 1,
                'purchpay_set-MIN_NUM_FORMS': 0, 'purchpay_set-MAX_NUM_FORMS': 1000,
                'purchpay_set-0-id': self.purchase.purchpay_set.get().pk,
                'purchpay_set-0-purch': self.purchase.pk, 'purchpay_set-0-name': 'Payment',
                'purchpay_set-0-percent_pay': 100, 'purchpay_set-0-lag_day_pay': 10,
                'purchpay_set-0-kind_lag_pay': ''}

    def test_shared_list_and_edit_ignore_scenario_and_need_no_scenarios(self):
        for scenario in self.scenarios:
            response = self.client.get(reverse('purch_list'), {'scenario': scenario.pk})
            self.assertEqual(list(response.context['purch_list']), [self.purchase])
            self.assertNotContains(response, 'id="workspace-scenario"')
            self.assertNotContains(response, 'copyPurchModal')
        self.assertRedirects(self.client.post(reverse('purch_edit', args=[self.purchase.pk]) + '?scenario=999', self.form_data()), reverse('purch_list'))
        self.purchase.refresh_from_db()
        self.assertEqual(self.purchase.lag_income, 77)
        ScenarioModel.objects.all().delete()
        self.assertTrue(Purch.objects.filter(pk=self.purchase.pk).exists())
        self.assertEqual(self.purchase.purchpay_set.count(), 1)
        self.assertContains(self.client.get(reverse('purch_list')), 'Supplier')
        self.assertEqual(self.client.get(reverse('purch_create')).status_code, 200)

    def test_duplicate_names_rejected_in_form_and_database(self):
        other = Purch.objects.create(name='Other', lag_income=10)
        response = self.client.post(reverse('purch_edit', args=[self.purchase.pk]), self.form_data(' other '))
        self.assertEqual(response.status_code, 200)
        self.assertIn('name', response.context['form'].errors)
        self.purchase.refresh_from_db()
        self.assertEqual(self.purchase.name, 'Supplier')
        with transaction.atomic(), self.assertRaises(IntegrityError):
            Purch.objects.create(name=' supplier ', lag_income=1)
        with transaction.atomic(), self.assertRaises(IntegrityError):
            Purch.objects.filter(pk=other.pk).update(name='SUPPLIER')

    def test_missing_names_and_blank_column_reported_before_sql_export(self):
        goods = PGGoods.objects.filter(scenario_plan=self.scenarios[0])
        goods.update(purch='Missing supplier')
        self.assertIn('Missing supplier', ' '.join(coverage_errors(self.scenarios[0])))
        response = self.client.get(reverse('purch_list'))
        self.assertContains(response, 'Missing supplier')
        self.assertContains(response, '?name=Missing%20supplier')
        with patch('model_purch.views.MS_SQL_CONN_STR', 'test'), patch('model_purch.views.pyodbc.connect') as connect:
            self.client.post(reverse('export_scenario_to_sql', args=[self.scenarios[0].pk]))
            connect.assert_not_called()
        self.assertFalse(ScenarioExport.objects.exists())
        goods.update(purch=None)
        self.assertIn('Не заполнена колонка', ' '.join(coverage_errors(self.scenarios[0])))
        goods.update(purch=' supplier ')
        self.assertEqual(coverage_errors(self.scenarios[0]), [])

    def test_shared_settings_and_payments_invalidate_every_export(self):
        for scenario in self.scenarios:
            parameters = snapshot(scenario)
            ScenarioExport.objects.create(scenario=scenario, parameters=parameters, fingerprint=fingerprint(parameters))
        for scenario in self.scenarios:
            self.assertEqual(calculation_readiness(scenario)[0], [])
        self.purchase.lag_income = 120
        self.purchase.save()
        for scenario in self.scenarios:
            self.assertIn('Повторите экспорт', ' '.join(calculation_readiness(scenario)[0]))
        for scenario in self.scenarios:
            parameters = snapshot(scenario)
            ScenarioExport.objects.create(scenario=scenario, parameters=parameters, fingerprint=fingerprint(parameters))
        self.purchase.purchpay_set.update(lag_day_pay=-10)
        for scenario in self.scenarios:
            self.assertIn('Повторите экспорт', ' '.join(calculation_readiness(scenario)[0]))

    def test_shared_directory_exported_under_each_scenario_name(self):
        for index, scenario in enumerate(self.scenarios):
            connection, cursor = Mock(), Mock()
            connection.cursor.return_value = cursor
            cursor.description = [('id',), ('planning_group',), ('scenario_name',)]
            cursor.fetchall.return_value = []
            cursor.fetchone.side_effect = [(500 + index,), (700 + index,)]
            with patch('model_purch.views.MS_SQL_CONN_STR', 'test'), patch('model_purch.views.pyodbc.connect', return_value=connection):
                self.client.post(reverse('export_scenario_to_sql', args=[scenario.pk]))
            connection.rollback.assert_not_called()
            merge = next(call for call in cursor.execute.call_args_list if 'MERGE INTO portal.Purch AS' in call.args[0])
            self.assertEqual(merge.args[1:], ('Supplier', 90, scenario.name))
            pay = next(call for call in cursor.execute.call_args_list if 'INSERT INTO portal.PurchPay (' in call.args[0])
            self.assertEqual(pay.args[1], 500 + index)
            self.assertEqual(scenario.exports.count(), 1)
        self.assertEqual(Purch.objects.count(), 1)

    def test_export_removes_obsolete_and_duplicate_settings_only_from_target_scenario(self):
        scenario = self.scenarios[0]
        connection, cursor = Mock(), Mock()
        connection.cursor.return_value = cursor
        cursor.description = [('id',), ('planning_group',), ('scenario_name',)]
        # First fetch prepares goods identity; second fetch reads purchases for the target scenario.
        cursor.fetchall.side_effect = [[], [(505, 'Supplier'), (504, ' supplier '), (503, 'Deleted')]]
        cursor.fetchone.side_effect = [(505,), (701,)]
        with patch('model_purch.views.MS_SQL_CONN_STR', 'test'), patch('model_purch.views.pyodbc.connect', return_value=connection):
            self.client.post(reverse('export_scenario_to_sql', args=[scenario.pk]))
        connection.rollback.assert_not_called()
        calls = cursor.execute.call_args_list
        deletes = [call.args[1:] for call in calls if call.args[0].startswith('DELETE FROM portal.Purch WHERE')]
        self.assertEqual(deletes, [(504, scenario.name), (503, scenario.name)])
        self.assertFalse(any("N'name_key'" in call.args[0] for call in calls))
        self.assertEqual(scenario.exports.count(), 1)

    def test_scenario_sync_preserves_user_settings_and_adds_missing_global_purchase(self):
        from .views import sync_purch_data
        connection, cursor = Mock(), Mock()
        connection.cursor.return_value = cursor
        cursor.description = [(n,) for n in ['PurchName', 'lag_income', 'PurchPayName', 'percent_pay', 'lag_day_pay']]
        cursor.fetchall.return_value = [(' supplier ', 120, 'Imported payment', 30, 20), ('New', 120, 'Payment', 100, 0)]
        with patch('model_purch.views.MS_SQL_CONN_STR', 'test'), patch('model_purch.views.pyodbc.connect', return_value=connection):
            sync_purch_data()
        self.purchase.refresh_from_db()
        self.assertEqual(self.purchase.lag_income, 90)
        self.assertEqual(self.purchase.purchpay_set.get().percent_pay, 100)
        self.assertEqual(Purch.objects.get(name='New').purchpay_set.get().percent_pay, 100)
        self.assertEqual(Purch.objects.count(), 2)
        connection.close.assert_called_once()
