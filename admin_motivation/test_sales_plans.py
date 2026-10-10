from base.testing import authorize_test_case
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
        authorize_test_case(self)
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
        self.cursor.description = [(name,) for name in ['Subdivision', 'PlanningGroupSalesERP', 'Date_', 'GroupERP', 'Brand', 'AmountUSD', *[f'USD_O{i}' for i in range(5)]]]
        self.cursor.fetchall.return_value = self.rows()
        self.read_offset = 0
        def execute(*args):
            self.read_offset = 0
        def fetchmany(size):
            rows = self.cursor.fetchall.return_value
            batch = rows[self.read_offset:self.read_offset + size]
            self.read_offset += len(batch)
            return batch
        self.cursor.execute.side_effect = execute
        self.cursor.fetchmany.side_effect = fetchmany

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
        self.version.subdivision_coefficients.filter(type_coeff__type_coeff_name='Продажи').delete()
        self.version.coefficients.all().update(motivation_coeff=.9)
        with self.assertRaisesMessage(ValidationError, 'Нет коэффициента'):
            calculate_plan(self.scenario.pk)
        self.assertEqual(self.scenario.lines.get(subdivision='').total_usd, Decimal('225'))

    def test_empty_invalid_or_failed_import_preserves_previous_data(self):
        self.load()
        calculate_plan(self.scenario.pk)
        for rows in [[], [('A', 'Sales', None, '*', '*', 1, 1, 0, 0, 0, 0)], [('A', 'Sales', date(2026, 1, 1), '*', '*', 100, 1, 0, 0, 0, 0)]]:
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
        self.assertIn('Итого мотивация USD', csv)
        self.assertIn(';1500;', csv)
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

    def test_user_query_column_order_with_string_date_loads_correctly(self):
        self.cursor.description = [(name,) for name in ['Subdivision', 'PlanningGroupSalesERP', 'GroupERP', 'Марка(Бренд)', 'Date_', 'AmountUSD', *[f'USD_O{i}' for i in range(5)]]]
        self.cursor.fetchall.return_value = [('A', 'Sales', '*', '*', '2025-01-01', 100, 10, 20, 30, 10, 30)]
        self.load()
        line = self.scenario.lines.get(subdivision='A')
        self.assertEqual(line.plan_date, date(2025, 1, 1))
        self.assertEqual(line.group, '*')
        self.assertEqual(line.brand, '*')
        calculate_plan(self.scenario.pk)
        self.assertEqual(self.scenario.lines.get(subdivision='').total_usd, Decimal('15'))

    def test_original_user_order_date_after_amount_and_reordered_segment_columns(self):
        self.cursor.description = [(name,) for name in ['Subdivision', 'PlanningGroupSalesERP', 'GroupERP', 'Марка(Бренд)', 'AmountUSD', 'Date_', 'USD_O4', 'USD_O3', 'USD_O2', 'USD_O1', 'USD_O0']]
        self.cursor.fetchall.return_value = [('A', 'Sales', '*', '*', 100, '2025-01-01 00:00:00', 30, 10, 30, 20, 10)]
        self.load()
        line = self.scenario.lines.get(subdivision='A')
        self.assertEqual(line.plan_date, date(2025, 1, 1))
        self.assertEqual(Decimal(line.segment_amounts['O1']), 20)
        self.assertEqual(Decimal(line.segment_amounts['O4']), 30)

    def test_missing_date_column_or_null_classification_has_specific_diagnostic(self):
        self.load()
        self.cursor.description[2] = ('MissingDate',)
        with self.assertRaisesMessage(ValidationError, 'date_'):
            self.load()
        self.cursor.description[2] = ('Date_',)
        for column, index in [('date_', 2), ('grouperp', 3), ('марка(бренд)', 4)]:
            row = list(self.rows()[0])
            row[index] = None
            self.cursor.fetchall.return_value = [tuple(row)]
            with self.assertRaises(ValidationError) as error:
                self.load()
            self.assertIn('Строка 1', str(error.exception))
            self.assertIn(column, str(error.exception).casefold())
            self.assertEqual(self.scenario.lines.count(), 3)

    def test_iso_date_datetime_and_invalid_date_values(self):
        for raw in ['2025-01-01', '2025-01-01T00:00:00', '2025-01-01 00:00:00.000', date(2025, 1, 1)]:
            row = list(self.rows()[0])
            row[2] = raw
            self.cursor.fetchall.return_value = [tuple(row)]
            self.load()
            self.assertEqual(self.scenario.lines.get(subdivision='A').plan_date, date(2025, 1, 1))
        row[2] = 'no date'
        self.cursor.fetchall.return_value = [tuple(row)]
        with self.assertRaisesMessage(ValidationError, 'Date_='):
            self.load()


    def test_empty_null_and_whitespace_group_use_other_without_losing_plan(self):
        for raw in [None, '', '   ', '9. OTHER']:
            row = list(self.rows()[0])
            row[1] = raw
            self.cursor.fetchall.return_value = [tuple(row)]
            self.load()
            self.assertEqual(self.scenario.lines.count(), 2)
            for line in self.scenario.lines.all():
                self.assertEqual(line.planning_group_sales, '9. OTHER')
                self.assertEqual(line.amount_usd, Decimal('1000'))

    def test_collapsed_other_rows_merge_local_and_global_and_calculate(self):
        self.good.planning_group_sales = '9. OTHER'
        self.good.save()
        rows = []
        for raw in [None, '', '  ', '9. OTHER']:
            row = list(self.rows()[0])
            row[1] = raw
            rows.append(tuple(row))
        row = list(self.rows()[1])
        row[1] = '9. OTHER'
        rows.append(tuple(row))
        self.cursor.fetchall.return_value = rows
        self.assertEqual(self.load(), 2)
        self.assertEqual(self.scenario.lines.count(), 3)
        self.assertEqual(self.scenario.lines.get(subdivision='A').amount_usd, Decimal('4000'))
        global_line = self.scenario.lines.get(subdivision='')
        self.assertEqual(global_line.amount_usd, Decimal('4500'))
        self.assertEqual(Decimal(global_line.segment_amounts['O2']), Decimal('1350'))
        calculate_plan(self.scenario.pk)
        self.assertEqual(self.scenario.lines.get(subdivision='').total_usd, Decimal('675'))
        self.assertEqual(self.scenario.lines.get(subdivision='A').total_usd, Decimal('920'))

    def test_identical_output_keys_from_normalized_sql_are_merged(self):
        self.cursor.fetchall.return_value = self.rows() * 2
        self.assertEqual(self.load(), 2)
        self.assertEqual(self.scenario.lines.count(), 3)
        self.assertEqual(self.scenario.lines.get(subdivision='').amount_usd, Decimal('3000'))
        self.assertIn("'9. OTHER') AS PlanningGroupSalesERP", SQL)

    def alternative_coefficient(self, model, value, kind='Политики', segment='K4', subdivision=None):
        good, _ = Goods.objects.get_or_create(goods_key='SalesOtherAlt', defaults={
            'planning_group_sales': 'Sales', 'group': 'Other group', 'brand': 'Alternative'})
        base = self.version.coefficients.filter(type_coeff__type_coeff_name=kind, segment__segment_name=segment).first()
        values = dict(version=self.version, goods=good, type_coeff=base.type_coeff, segment=base.segment,
                      motivation_coeff=value, manager_coeff=1, variation_calculate=base.variation_calculate)
        if subdivision:
            values['subdivision'] = subdivision
        return model.objects.create(**values)

    def test_global_fallback_minimum_across_goods_and_segments_within_kind(self):
        self.load()
        self.alternative_coefficient(GlobalCoeff, -.02)
        self.alternative_coefficient(GlobalCoeff, .01, kind='Продажи', segment='K2')
        self.alternative_coefficient(SubdivisionCoeff, -.9, subdivision=self.subs[0])
        self.scenario.lines.filter(subdivision='').update(brand='Unknown')
        calculate_plan(self.scenario.pk)
        line = self.scenario.lines.get(subdivision='')
        self.assertEqual(line.policies_usd, Decimal('-30'))
        self.assertEqual(line.sales_usd, Decimal('15'))
        self.assertEqual(line.total_usd, Decimal('-15'))
        policy = line.calculation['Политики']['O0']
        self.assertEqual(policy['source'], 'group_minimum')
        self.assertEqual(policy['source_segment'], 'O4')
        self.assertEqual(policy['source_brand'], 'Alternative')
        response = self.client.get(reverse('motivation_sales_plan', args=[self.scenario.pk]))
        self.assertContains(response, 'Итоги за весь период')
        csv = self.client.get(reverse('motivation_sales_plan', args=[self.scenario.pk])+'?export=details').content.decode('utf-8-sig')
        self.assertIn('group_minimum', csv)

    def test_subdivision_fallback_does_not_use_other_subdivision_or_global_minimum(self):
        self.load()
        self.alternative_coefficient(GlobalCoeff, -.9)
        self.alternative_coefficient(SubdivisionCoeff, -.8, subdivision=self.subs[1])
        self.alternative_coefficient(SubdivisionCoeff, .01, subdivision=self.subs[0])
        self.scenario.lines.filter(subdivision='A').update(brand='Unknown')
        calculate_plan(self.scenario.pk)
        line = self.scenario.lines.get(subdivision='A')
        self.assertEqual(line.policies_usd, Decimal('10'))
        self.assertEqual(line.sales_usd, Decimal('30'))
        self.assertEqual(line.total_usd, Decimal('40'))

    def test_exact_zero_and_other_exact_cells_override_minimum(self):
        self.load()
        self.alternative_coefficient(GlobalCoeff, -.9)
        self.version.coefficients.filter(type_coeff__type_coeff_name='Политики', segment__segment_name='K0').update(motivation_coeff=0)
        calculate_plan(self.scenario.pk)
        line = self.scenario.lines.get(subdivision='')
        self.assertEqual(line.policies_usd, Decimal('135'))
        self.assertEqual(line.calculation['Политики']['O0']['source'], 'exact')
        self.assertEqual(line.calculation['Политики']['O0']['coefficient'], '0.0')

    def test_group_fallback_uses_effective_version_before_table_fallback(self):
        self.load()
        self.alternative_coefficient(GlobalCoeff, -.02)
        future = create_version(date(2026, 10, 8), 'Future', self.version.pk)
        future.coefficients.all().update(motivation_coeff=-.9)
        self.scenario.lines.filter(subdivision='').update(brand='Unknown')
        calculate_plan(self.scenario.pk)
        line = self.scenario.lines.get(subdivision='')
        self.assertEqual(line.coefficient_version, self.version)
        self.assertEqual(line.policies_usd, Decimal('-30'))
        self.scenario.lines.filter(subdivision='').update(planning_group_sales='No matching group')
        calculate_plan(self.scenario.pk)
        line = self.scenario.lines.get(subdivision='')
        self.assertEqual(line.policies_usd, Decimal('-1350'))
        self.assertEqual(line.calculation['Политики']['O0']['source'], 'table_minimum')
        self.assertEqual(line.calculation['Политики']['O0']['source_version_id'], future.pk)


    def test_no_effective_version_uses_each_table_minimum_and_records_actual_version(self):
        self.cursor.fetchall.return_value = self.rows(date(2000, 1, 1))
        self.load()
        self.alternative_coefficient(GlobalCoeff, -.02)
        self.alternative_coefficient(SubdivisionCoeff, -.1, subdivision=self.subs[1])
        calculate_plan(self.scenario.pk)
        global_line = self.scenario.lines.get(subdivision='')
        local_line = self.scenario.lines.get(subdivision='A')
        self.assertIsNone(global_line.coefficient_version)
        self.assertEqual(global_line.policies_usd, Decimal('-30'))
        self.assertEqual(global_line.sales_usd, Decimal('75'))
        self.assertEqual(local_line.policies_usd, Decimal('-100'))
        self.assertEqual(local_line.sales_usd, Decimal('30'))
        cell = local_line.calculation['Политики']['O0']
        self.assertEqual(cell['source'], 'table_minimum')
        self.assertEqual(cell['source_subdivision'], 'B')
        self.assertEqual(cell['source_version_id'], self.version.pk)
        response = self.client.get(reverse('motivation_sales_plan', args=[self.scenario.pk]))
        self.assertContains(response, 'Итоги за весь период')

    def test_missing_type_in_entire_global_table_preserves_previous_result(self):
        self.load()
        calculate_plan(self.scenario.pk)
        self.version.coefficients.filter(type_coeff__type_coeff_name='Политики').delete()
        with self.assertRaises(ValidationError):
            calculate_plan(self.scenario.pk)
        self.assertEqual(self.scenario.lines.get(subdivision='').total_usd, Decimal('225'))

    def test_chunked_calculation_rolls_back_written_batches_on_late_error(self):
        self.load()
        template = self.scenario.lines.get(subdivision='A')
        SalesPlanLine.objects.bulk_create([SalesPlanLine(scenario=self.scenario, subdivision='A',
            plan_date=template.plan_date, planning_group_sales='Sales', group='*', brand=f'Batch{i}',
            amount_usd=1000, segment_amounts=template.segment_amounts, total_usd=42) for i in range(1100)])
        events = []
        def progress(data):
            events.append(data)
            if data['processed'] == 1000:
                raise RuntimeError('Failure after the first batch')
        with self.assertRaises(RuntimeError):
            calculate_plan(self.scenario.pk, progress=progress)
        self.assertEqual(self.scenario.lines.get(brand='Batch0').total_usd, Decimal('42'))
        self.assertEqual(self.scenario.lines.get(brand='Batch1099').total_usd, Decimal('42'))
        self.assertIsNone(self.scenario.lines.get(subdivision='').total_usd)
        self.assertEqual(events[-1]['total'], 1103)

    def test_progress_and_streamed_import_preserve_real_results(self):
        events = []
        self.assertEqual(load_plan(self.scenario.pk, self.connector, progress=events.append), 2)
        self.cursor.fetchall.assert_not_called()
        self.assertTrue(any(event['processed'] == 2 for event in events))
        events.clear()
        self.assertEqual(calculate_plan(self.scenario.pk, progress=events.append), 3)
        self.assertEqual(events[-1]['processed'], 3)
        self.assertEqual(events[-1]['percent'], 99)
        self.assertEqual(self.scenario.lines.get(subdivision='').total_usd, Decimal('225'))

    def test_stream_response_delivers_progress_completion_and_error(self):
        import json
        url = reverse('motivation_sales_plan', args=[self.scenario.pk])
        def operation(scenario_id, progress):
            progress({'stage': 'Working', 'percent': 50, 'processed': 1, 'total': 2})
            return 2
        with patch('admin_motivation.plan_views.load_plan', side_effect=operation):
            response = self.client.post(url, {'action': 'load'}, HTTP_ACCEPT='application/x-ndjson')
            events = [json.loads(line) for line in b''.join(response.streaming_content).decode().splitlines()]
        self.assertEqual(events[0]['percent'], 50)
        self.assertEqual(events[-1]['type'], 'complete')
        self.assertEqual(events[-1]['percent'], 100)
        with patch('admin_motivation.plan_views.calculate_plan', side_effect=ValidationError('Test failure')):
            response = self.client.post(url, {'action': 'calculate'}, HTTP_ACCEPT='application/x-ndjson')
            events = [json.loads(line) for line in b''.join(response.streaming_content).decode().splitlines()]
        self.assertEqual(events[-1]['type'], 'error')
        self.assertIn('Test failure', events[-1]['message'])

    def test_scenario_operation_rejects_duplicate_and_releases_after_failure(self):
        from .services.plan_operations import scenario_operation
        with self.assertRaises(RuntimeError):
            with scenario_operation(self.scenario.pk):
                with self.assertRaises(ValidationError):
                    with scenario_operation(self.scenario.pk):
                        self.fail('Concurrent operation was allowed')
                raise RuntimeError('Failure')
        with scenario_operation(self.scenario.pk):
            pass


    def test_period_report_sums_dated_results_after_each_version_is_applied(self):
        future = create_version(date(2026, 10, 8), 'Future', self.version.pk)
        future.coefficients.all().update(motivation_coeff=.3)
        self.cursor.fetchall.return_value = self.rows() + self.rows(date(2026, 10, 8))
        self.load()
        calculate_plan(self.scenario.pk)
        url = reverse('motivation_sales_plan', args=[self.scenario.pk])
        response = self.client.get(url)
        self.assertEqual(len(response.context['report_rows']), 1)
        self.assertEqual(response.context['totals']['total'], Decimal('1125'))
        self.assertEqual(response.context['totals']['plan'], Decimal('3000'))
        root = response.context['report_nodes'][0]
        self.assertEqual(root['total'], Decimal('1125'))
        self.assertEqual(root['children'][0]['children'][0]['children'][0]['total'], Decimal('1125'))
        self.assertNotContains(response, '<th>Дата</th>')
        local = self.client.get(url, {'scope': 'subdivisions', 'subdivision': 'A'})
        self.assertEqual(len(local.context['report_nodes']), 1)
        self.assertEqual(local.context['totals']['total'], Decimal('460'))

    def test_filters_limit_report_subtotals_and_period_csv_consistently(self):
        self.cursor.fetchall.return_value = self.rows() + [('A', 'Other Sales', date(2026, 10, 1), 'ERP & Group', 'Brand X', 100, 20, 20, 20, 20, 20)]
        self.load()
        calculate_plan(self.scenario.pk)
        url = reverse('motivation_sales_plan', args=[self.scenario.pk])
        filters = {'scope': 'subdivisions', 'subdivision': 'A', 'planning_group_sales': 'Other Sales', 'group': 'ERP & Group', 'brand': 'Brand X'}
        response = self.client.get(url, filters)
        self.assertEqual(response.context['totals']['plan'], Decimal('100'))
        self.assertEqual(response.context['totals']['total'], Decimal('23'))
        self.assertIn('ERP+%26+Group', response.context['export_url'])
        export = self.client.get(url, {**filters, 'export': 'csv'}).content.decode('utf-8-sig')
        import csv, io
        rows = list(csv.reader(io.StringIO(export), delimiter=';'))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1][:4], ['A', 'Other Sales', 'ERP & Group', 'Brand X'])
        self.assertEqual(Decimal(rows[1][-1]), Decimal('23'))
        self.assertNotIn('Дата', rows[0])
        self.assertNotIn('2026-10-01', export)
        self.assertNotIn('\ufeff', export)
        empty = self.client.get(url, {'brand': 'Does not exist'})
        self.assertEqual(empty.context['report_nodes'], [])
        self.assertContains(empty, 'Нет данных по выбранным фильтрам')

    def test_uncalculated_report_preserves_none_instead_of_showing_zero_motivation(self):
        self.load()
        response = self.client.get(reverse('motivation_sales_plan', args=[self.scenario.pk]))
        self.assertEqual(response.context['totals']['plan'], Decimal('1500'))
        self.assertIsNone(response.context['totals']['total'])
        self.assertEqual(response.context['pending_count'], 1)
        self.assertContains(response, 'Не рассчитано строк: 1')

    def test_period_report_uses_one_aggregation_query(self):
        from .services.plan_report import period_report
        self.load()
        with self.assertNumQueries(1):
            report = period_report(self.scenario, 'subdivisions', {})
        self.assertEqual(len(report['report_nodes']), 2)
        self.assertEqual(report['filter_choices']['subdivision'], ['A', 'B'])
