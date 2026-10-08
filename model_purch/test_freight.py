from base.testing import authorize_test_case
from decimal import Decimal
from unittest.mock import Mock, patch

from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse

from .models import Freight, ScenarioModel


class FreightTests(TestCase):
    def setUp(self):
        authorize_test_case(self)
        self.source = ScenarioModel.objects.create(name='Source', date_start_plan='2026-01-01', date_end_plan='2026-12-31')
        self.target = ScenarioModel.objects.create(name='Target', date_start_plan='2027-01-01', date_end_plan='2027-12-31')
        self.values = {'price_per_container': Decimal('1234.56'), 'volume_per_container': Decimal('67.890')}
        self.freight = Freight.objects.create(scenario=self.source, **self.values)

    def test_save_uses_posted_scenario_and_updates_existing_row(self):
        for price in ['2345.67', '3456.78']:
            response = self.client.post(reverse('freight'), {'scenario': self.source.pk,
                'price_per_container': price, 'volume_per_container': '75.125'})
            self.assertRedirects(response, reverse('pggoods_list') + f'?scenario={self.source.pk}#freight-settings')
        self.assertEqual(Freight.objects.count(), 1)
        self.freight.refresh_from_db()
        self.assertEqual(self.freight.price_per_container, Decimal('3456.78'))
        self.assertEqual(self.freight.volume_per_container, self.values['volume_per_container'])
        self.assertFalse(Freight.objects.filter(scenario=self.target).exists())

    def test_invalid_values_or_target_do_not_modify_data(self):
        for data in [
            {'scenario': self.source.pk, 'price_per_container': '-1', 'volume_per_container': '50'},
            {'scenario': self.source.pk, 'price_per_container': '10.001', 'volume_per_container': '50'},
            {'price_per_container': '10', 'volume_per_container': '50'},
            {'scenario': 'invalid', 'price_per_container': '10', 'volume_per_container': '50'},
        ]:
            self.client.post(reverse('freight'), data)
        self.freight.refresh_from_db()
        self.assertEqual(self.freight.price_per_container, self.values['price_per_container'])
        self.assertEqual(Freight.objects.count(), 1)

    def test_copy_creates_and_overwrites_target_without_changing_source(self):
        data = {'scenario': self.target.pk, 'source_scenario_id': self.source.pk}
        self.client.post(reverse('copy_freight'), data)
        target = Freight.objects.get(scenario=self.target)
        Freight.objects.filter(pk=target.pk).update(price_per_container=0)
        self.client.post(reverse('copy_freight'), data)
        target.refresh_from_db()
        self.assertEqual(target.price_per_container, self.values['price_per_container'])
        self.assertEqual(target.volume_per_container, Decimal('65'))
        self.assertEqual(Freight.objects.count(), 2)
        self.freight.refresh_from_db()
        self.assertEqual(self.freight.price_per_container, target.price_per_container)

    def test_invalid_copy_and_empty_source_preserve_target(self):
        for data in [
            {'scenario': self.source.pk, 'source_scenario_id': self.target.pk},
            {'scenario': self.source.pk, 'source_scenario_id': self.source.pk},
            {'scenario': self.source.pk, 'source_scenario_id': 'invalid'},
            {'source_scenario_id': self.source.pk},
        ]:
            self.client.post(reverse('copy_freight'), data)
        self.assertEqual(Freight.objects.count(), 1)
        self.freight.refresh_from_db()
        self.assertEqual(self.freight.price_per_container, self.values['price_per_container'])
        self.assertEqual(self.client.get(reverse('copy_freight')).status_code, 405)

    def test_page_displays_selected_scenario_and_handles_no_scenarios(self):
        response = self.client.get(reverse('pggoods_list'), {'scenario': self.source.pk})
        self.assertContains(response, '1234.56')
        self.assertContains(response, 'Копировать цену фрахта из сценария')
        self.assertNotContains(response, 'name="volume_per_container"')
        self.assertNotContains(response, '>Фрахт</a>')
        self.assertEqual(response.context['current_scenario'], self.source)
        self.assertRedirects(self.client.get(reverse('freight'), {'scenario': self.source.pk}), reverse('pggoods_list') + f'?scenario={self.source.pk}#freight-settings')
        ScenarioModel.objects.all().delete()
        response = self.client.get(reverse('freight'))
        self.assertRedirects(response, reverse('pggoods_list') + '#freight-settings')

    def test_inline_validation_preserves_price_and_product_container_volumes(self):
        response = self.client.post(reverse('freight'), {'scenario': self.source.pk, 'price_per_container': '-1'})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="freight-settings"')
        self.assertTrue(response.context['freight_form'].errors)
        self.freight.refresh_from_db()
        self.assertEqual(self.freight.price_per_container, self.values['price_per_container'])

    def test_database_enforces_unique_scenario_and_valid_values(self):
        for scenario, price, volume in [(self.source, 1, 1), (self.target, -1, 1), (self.target, 1, 0)]:
            with self.assertRaises(IntegrityError), transaction.atomic():
                Freight.objects.create(scenario=scenario, price_per_container=price, volume_per_container=volume)

    @patch('model_purch.views.MS_SQL_CONN_STR', 'test')
    @patch('model_purch.views.pyodbc.connect')
    def test_sql_export_preserves_precision_and_scenario_identity(self, connect):
        connection, cursor = Mock(), Mock()
        connect.return_value = connection
        connection.cursor.return_value = cursor
        cursor.description = [('id',), ('planning_group',), ('scenario_name',)]
        cursor.fetchall.return_value = []
        response = self.client.post(reverse('export_scenario_to_sql', args=[self.source.pk]))
        self.assertEqual(response.status_code, 302)
        connection.rollback.assert_not_called()
        call = next(call for call in cursor.execute.call_args_list if 'MERGE INTO portal.Freight' in call.args[0])
        self.assertEqual(call.args[1:], (self.source.pk, self.source.name, '1234.56', '67.890'))
        self.assertIn('ON target.scenario_id = source.scenario_id', call.args[0])
        self.assertEqual(call.args[0].count('?'), len(call.args) - 1)
