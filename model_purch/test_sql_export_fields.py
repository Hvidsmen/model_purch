from base.testing import authorize_test_case
from unittest.mock import Mock, patch

from django.db import models
from django.test import SimpleTestCase, TestCase
from django.test.utils import isolate_apps
from django.urls import reverse

from .models import KindLagPay, Purch, PurchPay, ScenarioModel
from .sql_export_fields import ensure_model_columns, export_additional_fields


class NewFieldExportTests(TestCase):
    def setUp(self):
        authorize_test_case(self)
        self.scenario = ScenarioModel.objects.create(name='Plan', date_start_plan='2026-01-01', date_end_plan='2026-12-31')
        self.kind = KindLagPay.objects.create(name='От даты поступления')
        self.purch = Purch.objects.create(name='Supplier', lag_income=90, lage_make=35)
        self.pay = PurchPay.objects.create(purch=self.purch, name='Advance', percent_pay=30,
                                          lag_day_pay=-10, kind_lag_pay=self.kind)

    def connection(self):
        connection, cursor = Mock(), Mock()
        connection.cursor.return_value = cursor
        cursor.description = [('id',), ('planning_group',), ('scenario_name',)]
        cursor.fetchall.return_value = []
        cursor.fetchone.side_effect = [(501,), (701,)]
        return connection, cursor

    @patch('model_purch.views.MS_SQL_CONN_STR', 'test')
    @patch('model_purch.views.pyodbc.connect')
    def test_existing_tables_receive_new_columns_and_values(self, connect):
        connection, cursor = self.connection()
        connect.return_value = connection
        response = self.client.post(reverse('export_scenario_to_sql', args=[self.scenario.pk]))
        self.assertEqual(response.status_code, 302)
        connection.rollback.assert_not_called()
        calls = cursor.execute.call_args_list
        self.assertTrue(any("COL_LENGTH(N'portal.Purch', N'lage_make')" in call.args[0] for call in calls))
        self.assertTrue(any("COL_LENGTH(N'portal.PurchPay', N'kind_lag_pay_id')" in call.args[0] for call in calls))
        reference = next(call for call in calls if 'MERGE INTO portal.KindLagPay' in call.args[0])
        self.assertEqual(reference.args[1:], (self.kind.pk, self.kind.name))
        purchase = next(call for call in calls if call.args[0].startswith('UPDATE [portal].[Purch]'))
        self.assertEqual(purchase.args[1:], (35, 'p2', 501))
        self.assertIn('[lage_make] = ?', purchase.args[0])
        payment = next(call for call in calls if 'INSERT INTO portal.PurchPay (' in call.args[0])
        self.assertEqual(payment.args[1:], (501, 'Advance', 30.0, -10, self.kind.pk, self.kind.name))
        self.assertEqual(payment.args[0].count('?'), len(payment.args)-1)
        self.assertLess(calls.index(reference), calls.index(payment))

    @patch('model_purch.views.MS_SQL_CONN_STR', 'test')
    @patch('model_purch.views.pyodbc.connect')
    def test_optional_payment_kind_exports_null_without_dropping_payment(self, connect):
        self.pay.kind_lag_pay = None
        self.pay.save()
        connection, cursor = self.connection()
        connect.return_value = connection
        self.client.post(reverse('export_scenario_to_sql', args=[self.scenario.pk]))
        payment = next(call for call in cursor.execute.call_args_list if 'INSERT INTO portal.PurchPay (' in call.args[0])
        self.assertEqual(payment.args[-2:], (None, None))
        connection.rollback.assert_not_called()

    @patch('model_purch.views.MS_SQL_CONN_STR', 'test')
    @patch('model_purch.views.pyodbc.connect')
    def test_failure_to_save_new_field_rolls_back_export(self, connect):
        connection, cursor = self.connection()
        connect.return_value = connection
        def execute(sql, *args):
            if sql.startswith('UPDATE [portal].[Purch]'):
                raise RuntimeError('new field write failed')
        cursor.execute.side_effect = execute
        self.client.post(reverse('export_scenario_to_sql', args=[self.scenario.pk]))
        connection.rollback.assert_called_once()
        connection.close.assert_called_once()
        self.assertFalse(any('INSERT INTO portal.PurchPay (' in call.args[0] for call in cursor.execute.call_args_list))


class FutureFieldExportTests(SimpleTestCase):
    @isolate_apps('model_purch')
    def test_future_scalar_fields_are_added_and_exported_without_changing_view(self):
        class FuturePurch(models.Model):
            name = models.CharField(max_length=255)
            lag_income = models.IntegerField()
            custom_lead_time = models.IntegerField(null=True)
            note = models.CharField(max_length=120, null=True)
            settings_json = models.JSONField(null=True)
            class Meta:
                app_label = 'model_purch'
        cursor = Mock()
        ensure_model_columns(cursor, 'Purch', FuturePurch, set())
        sql = '\n'.join(call.args[0] for call in cursor.execute.call_args_list)
        self.assertIn('ADD [custom_lead_time] INT NULL', sql)
        self.assertIn('ADD [note] NVARCHAR(120) NULL', sql)
        self.assertIn('ADD [settings_json] NVARCHAR(MAX) NULL', sql)
        item = FuturePurch(name='Supplier', lag_income=90, custom_lead_time=12, note=None, settings_json={'enabled': True})
        export_additional_fields(cursor, 'Purch', item, {'name', 'lag_income'}, {'id': 501})
        update = cursor.execute.call_args.args
        self.assertEqual(update[1:], (12, None, '{"enabled": true}', 501))
        self.assertIn('[custom_lead_time] = ?', update[0])
        self.assertIn('[note] = ?', update[0])

    @isolate_apps('model_purch')
    def test_new_relation_requires_explicit_mapping_instead_of_silent_omission(self):
        class FuturePurch(models.Model):
            another_relation = models.ForeignKey('self', null=True, on_delete=models.SET_NULL)
            class Meta:
                app_label = 'model_purch'
        cursor = Mock()
        with self.assertRaisesRegex(ValueError, 'another_relation'):
            ensure_model_columns(cursor, 'Purch', FuturePurch, set())
        cursor.execute.assert_not_called()
