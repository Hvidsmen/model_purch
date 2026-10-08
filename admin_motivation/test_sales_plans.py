from datetime import date
from decimal import Decimal
from unittest.mock import Mock, patch
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.test import TestCase
from django.urls import reverse
from .models import (SalesPlanScenario, SalesPlanLine, GlobalCoeffVersion, GlobalCoeff, SubdivisionCoeff,
                     Chanel, Subdivision, Goods, TypeCoeff, SegmentCoeff, VariationCalculate)
from .services.sales_plans import load_plan, calculate_plan, source_versions, SQL
from .services.versions import create_version


class SalesPlanTests(TestCase):
    def setUp(self):
        self.version = GlobalCoeffVersion.objects.get(effective_from=date(2001, 1, 1))
        self.scenario = SalesPlanScenario.objects.create(title='Budget', source_version="Plan ' version")
        channel = Chanel.objects.create(chanel_name='Dealer')
        self.subs = [Subdivision.objects.create(subdivision_key=key, subdivision_name=key, subdivision_global=key, chanel=channel) for key in ['A', 'B']]
        self.good = Goods.objects.create(goods_key='Sales**', planning_group_sales='Sales', group='*', brand='*')
        variation, _ = VariationCalculate.objects.get_or_create(variation_name='Level')
        for kind_name, global_k, local_k in [('Политики', '.1', '.2'), ('Продажи', '.05', '.03')]:
            kind = TypeCoeff.objects.create(type_coeff_name=kind_name)
            for i in range(5):
                segment, _ = SegmentCoeff.objects.get_or_create(segment_name=f'K{i}')
                base = dict(version=self.version, goods=self.good, type_coeff=kind, segment=segment, variation_calculate=variation, manager_coeff=1)
                GlobalCoeff.objects.create(**base, motivation_coeff=float(global_k))
                for sub in self.subs:
                    SubdivisionCoeff.objects.create(**base, subdivision=sub, motivation_coeff=float(local_k))
        self.connection, self.cursor = Mock(), Mock()
        self.connector = Mock(return_value=(self.connection, self.cursor))
        self.cursor.fetchall.return_value = self.rows()

    def rows(self, on_date=date(2026, 10, 1)):
        return [('A', 'Sales', on_date, '*', '*', Decimal('1000'), Decimal('100'), Decimal('200'), Decimal('300'), Decimal('100'), Decimal('300')),
                ('B', 'Sales', on_date, '*', '*', Decimal('500'), Decimal('50'), Decimal('100'), Decimal('150'), Decimal('50'), Decimal('150'))]

    def load(self):
        return load_plan(self.scenario.pk, self.connector)

    def test_parameterized_read_only_import_and_global_aggregation(self):
        self.assertEqual(self.load(), 2)
        self.cursor.execute.assert_called_once_with(SQL, self.scenario.source_version)
        self.assertNotIn(self.scenario.source_version, SQL)
        self.connection.commit.assert_not_called()
        self.connection.close.assert_called_once()
        global_line = self.scenario.lines.get(subdivision='')
        self.assertEqual(global_line.amount_usd, Decimal('1500'))
        self.assertEqual(Decimal(global_line.segment_amounts['O2']), Decimal('450'))
        self.assertEqual(self.scenario.lines.count(), 3)

    def test_global_and_local_coefficients_separate_and_snapshot_saved(self):
        self.load()
        self.assertEqual(calculate_plan(self.scenario.pk), 3)
        global_line = self.scenario.lines.get(subdivision='')
        local = self.scenario.lines.get(subdivision='A')
        self.assertEqual(global_line.policies_usd, Decimal('150'))
        self.assertEqual(global_line.sales_usd, Decimal('75'))
        self.assertEqual(global_line.total_usd, Decimal('225'))
        self.assertEqual(local.total_usd, Decimal('230'))
        self.assertEqual(local.calculation['Политики']['O2']['coefficient'], '0.2')
        GlobalCoeff.objects.filter(version=self.version).update(motivation_coeff=.9)
        self.assertEqual(self.scenario.lines.get(subdivision='').total_usd, Decimal('225'))

    def test_each_plan_date_selects_its_effective_version(self):
        future = create_version(date(2026, 10, 8), 'Future', self.version.pk)
        future.coefficients.all().update(motivation_coeff=.3)
        self.cursor.fetchall.return_value = self.rows() + self.rows(date(2026, 10, 8))
        self.load()
        calculate_plan(self.scenario.pk)
        before = self.scenario.lines.get(subdivision='', plan_date=date(2026, 10, 1))
        after = self.scenario.lines.get(subdivision='', plan_date=date(2026, 10, 8))
        self.assertEqual(before.coefficient_version, self.version)
        self.assertEqual(after.coefficient_version, future)
        self.assertEqual(after.total_usd, Decimal('900'))

    def test_missing_coefficients_roll_back_every_result(self):
        self.load()
        calculate_plan(self.scenario.pk)
        self.version.subdivision_coefficients.filter(subdivision=self.subs[1]).delete()
        self.version.coefficients.all().update(motivation_coeff=.9)
        with self.assertRaisesMessage(ValidationError, 'Нет коэффициента'):
            calculate_plan(self.scenario.pk)
        self.assertEqual(self.scenario.lines.get(subdivision='').total_usd, Decimal('225'))

    def test_empty_invalid_duplicate_or_failed_import_preserves_previous_data(self):
        self.load()
        calculate_plan(self.scenario.pk)
        for rows in [[], [('A', 'Sales', None, '*', '*', 1, 1, 0, 0, 0, 0)], [('A', 'Sales', date(2026, 1, 1), '*', '*', 100, 1, 0, 0, 0, 0)], self.rows()*2]:
            self.cursor.fetchall.return_value = rows
            with self.assertRaises((ValidationError, IntegrityError)):
                self.load()
            self.assertEqual(self.scenario.lines.count(), 3)
            self.assertEqual(self.scenario.lines.get(subdivision='').total_usd, Decimal('225'))
        self.cursor.execute.side_effect = RuntimeError('SQL unavailable')
        with self.assertRaises(RuntimeError):
            self.load()
        self.assertEqual(self.scenario.lines.count(), 3)

    def test_reload_is_idempotent_and_resets_calculation(self):
        self.load()
        calculate_plan(self.scenario.pk)
        self.load()
        self.assertEqual(self.scenario.lines.count(), 3)
        self.assertFalse(self.scenario.lines.exclude(total_usd=None).exists())
        self.scenario.refresh_from_db()
        self.assertIsNone(self.scenario.calculated_at)

    def test_scenarios_are_isolated(self):
        self.load()
        other = SalesPlanScenario.objects.create(title='Other', source_version='Other')
        load_plan(other.pk, self.connector)
        calculate_plan(other.pk)
        self.assertFalse(self.scenario.lines.exclude(total_usd=None).exists())

    def test_ui_create_load_calculate_scopes_export_and_no_get_sql(self):
        response = self.client.post(reverse('motivation_sales_plans'), {'action': 'create', 'title': 'New', 'source_version': '2027'})
        self.assertEqual(response.status_code, 302)
        url = reverse('motivation_sales_plan', args=[self.scenario.pk])
        with patch('admin_motivation.services.sales_plans.connect_database', self.connector):
            self.assertEqual(self.client.get(url).status_code, 200)
            self.connector.assert_not_called()
            self.client.post(url, {'action': 'load'})
            self.client.post(url, {'action': 'calculate'})
        global_response = self.client.get(url)
        self.assertContains(global_response, 'Глобальный план')
        self.assertEqual(global_response.context['totals']['total'], Decimal('225'))
        local_response = self.client.get(url+'?scope=subdivisions')
        self.assertEqual(local_response.context['totals']['total'], Decimal('345'))
        csv = self.client.get(url+'?export=csv').content.decode('utf-8-sig')
        self.assertIn('Политики k O0', csv)
        self.assertIn('1500.000000', csv)
        self.assertEqual(self.client.get(reverse('motivation_sales_plan', args=[999999])).status_code, 404)

    def test_source_versions_and_connection_cleanup(self):
        self.cursor.fetchall.return_value = [('2026',), ('2027',)]
        self.assertEqual(source_versions(self.connector), ['2026', '2027'])
        self.connection.close.assert_called_once()

    def test_negative_coefficients_and_zero_plan_are_valid(self):
        self.version.coefficients.all().update(motivation_coeff=-.1)
        self.cursor.fetchall.return_value = [('A', 'Sales', date(2026, 1, 1), '*', '*', 0, 0, 0, 0, 0, 0)]
        self.load()
        calculate_plan(self.scenario.pk)
        self.assertEqual(self.scenario.lines.get(subdivision='').total_usd, Decimal(0))

    def test_sql_preserves_date_and_matches_names_and_wildcard_classification(self):
        self.assertIn('CAST(ps.Date_ AS date)', SQL)
        self.assertIn('s.SubdivisionName Subdivision', SQL)
        self.assertIn("ELSE '*' END", SQL)
        self.assertIn('plan_.Subdivision=f.SubdivisionName', SQL)
        self.assertIn('COALESCE(f.O0,1)', SQL)
