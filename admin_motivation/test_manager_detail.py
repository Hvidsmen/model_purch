from decimal import Decimal
from unittest.mock import Mock, patch
from django.test import TestCase
from django.http import QueryDict
from django.urls import reverse
from django.core.exceptions import ValidationError
from base.testing import authorize_test_case
from .services.reports import parameters, load_report, bind_sql
from .services.manager_detail import manager_detail_report, metrics, grouped_sql, period_motivation_summary


def detail_rows():
    rows = []
    for period in ['1 Квартал', 'Год']:
        rows.append({'Подразделение': 'A', 'АвторСделки': 'Иванов', 'ГруппаПлановПродаж': '1. RAC',
            'ГруппаТоваров': 'Кондиционеры', 'Марка': '<Brand>', 'Год': 2026, 'КварталМотивации': period,
            **{f'O{i}': 100 for i in range(5)}, 'СуммаПланUSDКвартал': 1000, 'СуммаОтгрузокUSDКвартал': 800,
            'СуммаПланUSD': 600, 'СуммаОтгрузокUSD': 540, 'СуммаМотивацииUSD': 100,
            'СуммаМотивацииUSDУчетПроцента': 90, 'Остаток': 5, 'СуммаМотивацииRUR': 10000,
            'СуммаМотивацииRURУчетПроцента': 9000, 'ОстатокRUR': 500,
            **{f'K{i}WeightedSum': Decimal('1.5') for i in range(5)}, **{f'K{i}Weight': 100 for i in range(5)}})
    return rows


class ManagerDetailTests(TestCase):
    def setUp(self):
        authorize_test_case(self)

    def test_annual_inputs_and_combined_motivation_in_both_currencies(self):
        data = manager_detail_report(detail_rows())
        self.assertEqual(data['periods'], ['1 Квартал', 'Год'])
        self.assertEqual(data['years'], ['2026'])
        period, annual, overall = data['detail_totals']
        self.assertEqual(period[10:14], [Decimal(1000), Decimal(800), Decimal('.8'), Decimal(100)])
        self.assertEqual(overall[5:10], [Decimal(100)] * 5)  # Overall inputs come from the annual row.
        self.assertEqual(overall[10:14], [Decimal(1000), Decimal(800), Decimal('.8'), Decimal(200)])
        self.assertEqual(overall[17:], [Decimal(180), Decimal(10), Decimal(190)])
        self.assertEqual(manager_detail_report(detail_rows(), 'RUB')['cards'][0]['total'], Decimal(19000))
        labels = []
        node = data['nodes'][0]
        while True:
            labels.append(node['label'])
            if not node['children']: break
            node = node['children'][0]
        self.assertEqual(labels, ['A', 'Иванов', '1. RAC', 'Кондиционеры', '<Brand>'])
        self.assertEqual(node['values'][0][0], Decimal('.015'))
        self.assertEqual(data['nodes'][0]['values'][0][0], Decimal('.015'))
        rubles = manager_detail_report(detail_rows(), 'RUB')['detail_totals'][-1]
        self.assertEqual(rubles[13], Decimal(20000))
        self.assertEqual(rubles[17:], [Decimal(18000), Decimal(1000), Decimal(19000)])
        self.assertEqual(rubles[10:13], overall[10:13])  # Plan/fact always USD.

    def test_overall_uses_annual_execution_at_every_level_and_keeps_payments(self):
        rows = detail_rows()
        rows[0].update(СуммаПланUSD=1000, СуммаОтгрузокUSD=950,
                       СуммаПланUSDКвартал=1000, СуммаОтгрузокUSDКвартал=950, O0=900)
        rows[1].update(СуммаПланUSD=4000, СуммаОтгрузокUSD=3160,
                       СуммаПланUSDКвартал=4000, СуммаОтгрузокUSDКвартал=3160, O0=3000)
        for currency in ['USD', 'RUB']:
            data = manager_detail_report(rows, currency)
            def check(blocks):
                annual, overall = blocks[1:]
                self.assertEqual(overall[5:13], annual[5:13])
                self.assertEqual(overall[14:17], annual[14:17])
                self.assertEqual(overall[16], Decimal('.79'))
                self.assertEqual(overall[17], sum(block[17] for block in blocks[:-1]))
                self.assertEqual(overall[19], overall[17] + overall[18])
            check(data['detail_totals'])
            node = data['nodes'][0]
            while True:
                check(node['values'])
                if not node['children']: break
                node = node['children'][0]
        missing = manager_detail_report(rows[:1])['detail_totals'][-1]
        self.assertTrue(all(value is None for value in missing[5:13]))
        self.assertTrue(all(value is None for value in missing[14:17]))
        self.assertEqual(missing[19], Decimal(95))
        extra = dict(rows[0], Марка='Without annual data')
        incomplete = manager_detail_report([*rows, extra])
        self.assertIsNone(incomplete['detail_totals'][-1][16])
        complete_brand = incomplete['nodes'][0]['children'][0]['children'][0]['children'][0]['children'][0]
        self.assertEqual(complete_brand['values'][-1][16], Decimal('.79'))

    def test_coefficients_are_shipment_weighted_at_every_level(self):
        first = detail_rows()[0]
        first.update(K0WeightedSum=Decimal(1), K0Weight=100,
                     K1WeightedSum=Decimal(4), K1Weight=200,
                     K2WeightedSum=Decimal(0), K2Weight=100,
                     K3WeightedSum=Decimal(0), K3Weight=0)
        second = dict(first, Марка='Second', K0WeightedSum=Decimal(27), K0Weight=900,
                      K1WeightedSum=Decimal(32), K1Weight=800)
        annual_first = dict(first, КварталМотивации='Год', K0WeightedSum=Decimal(1), K0Weight=50)
        annual_second = dict(second, КварталМотивации='Год', K0WeightedSum=Decimal(6), K0Weight=150)
        data = manager_detail_report([first, second, annual_first, annual_second])
        self.assertEqual(data['detail_totals'][0][0], Decimal('.028'))
        self.assertEqual(data['detail_totals'][0][1], Decimal('.036'))
        self.assertEqual(data['detail_totals'][-1][0], Decimal('.035'))
        node = data['nodes'][0]
        while node['children']:
            self.assertEqual(node['values'][0][0], Decimal('.028'))
            self.assertEqual(node['values'][0][1], Decimal('.036'))
            self.assertEqual(node['values'][0][2], Decimal(0))
            self.assertIsNone(node['values'][0][3])
            node = node['children'][0]
        self.assertEqual(node['values'][0][0], Decimal('.01'))
        cancelled = metrics([dict(first, K0Weight=100, K0WeightedSum=1),
                             dict(first, K0Weight=-100, K0WeightedSum=-1)])
        self.assertIsNone(cancelled[0])

    def test_summary_groups_by_branch_and_author_in_selected_currency(self):
        rows = detail_rows()
        rows.extend([dict(rows[0], АвторСделки='Петров'), dict(rows[0], Подразделение='B')])
        for currency in ['USD', 'RUB']:
            data = manager_detail_report(rows, currency)
            self.assertEqual([card['currency'] for card in data['cards']], [currency])
            summary = data['period_summary']
            self.assertEqual([(row['subdivision'], row['author']) for row in summary['rows']],
                             [('A', 'Иванов'), ('A', 'Петров'), ('B', 'Иванов')])
            self.assertEqual(summary['currency'], currency)
            self.assertEqual(sum(row['total'] for row in summary['rows']), summary['totals']['total'])
            self.assertEqual(summary['totals']['total'], data['cards'][0]['total'])
            for index in range(len(summary['columns'])):
                values = [row['values'][index] for row in summary['rows'] if row['values'][index] is not None]
                self.assertEqual(sum(values) if values else None, summary['totals']['values'][index])

    def test_sql_weights_use_each_segment_and_ignore_missing_coefficients(self):
        import sqlite3
        from .services.manager_detail import DIMENSIONS, SUM_FIELDS
        columns = [*DIMENSIONS, 'Год', 'КварталМотивации', 'VariationCalculate', *SUM_FIELDS, 'K0', 'K1', 'K2', 'k3', 'k4']
        database = sqlite3.connect(':memory:')
        try:
            database.execute('CREATE TABLE source (' + ','.join('[' + key + ']' for key in columns) + ')')
            first = dict(detail_rows()[0], K0=.01, K1=None, K2=0, k3=.04, k4=.05, O0=100, O1=500, O2=100)
            second = dict(first, K0=.03, K1=.02, O0=900, O1=100)
            for row in [first, second]:
                database.execute('INSERT INTO source VALUES (' + ','.join('?' for key in columns) + ')', [row.get(key) for key in columns])
            cursor = database.execute(grouped_sql('SELECT * FROM source'))
            result = [dict(zip([column[0] for column in cursor.description], row)) for row in cursor.fetchall()]
            values = metrics(result)
            self.assertEqual(values[0], Decimal('.028'))
            self.assertEqual(values[1], Decimal('.02'))
            self.assertEqual(values[2], Decimal(0))
        finally:
            database.close()

    def test_summary_hierarchy_keeps_authors_within_subdivision_and_totals(self):
        rows = detail_rows()
        other = dict(rows[0], Подразделение='B', АвторСделки='Петров')
        summary = period_motivation_summary([*rows, other])
        self.assertEqual([group['subdivision'] for group in summary['groups']], ['A', 'B'])
        self.assertEqual([row['author'] for row in summary['groups'][1]['authors']], ['Петров'])
        self.assertEqual(sum(group['total'] for group in summary['groups']), summary['totals']['total'])
        for group in summary['groups']:
            self.assertEqual(sum(row['total'] for row in group['authors']), group['total'])

    def test_period_summary_matches_cards_in_both_currencies_and_keeps_all_periods(self):
        from .services.manager_detail import period_motivation_summary
        rows = detail_rows()
        rows.extend([dict(rows[0], КварталМотивации='2 Квартал(1-2 кв)',
                          СуммаМотивацииUSDУчетПроцента=0, СуммаМотивацииRURУчетПроцента=0, Остаток=0, ОстатокRUR=0),
                     dict(rows[0], КварталМотивации='3 Квартал', СуммаМотивацииUSDУчетПроцента=-10,
                          СуммаМотивацииRURУчетПроцента=-1000, Остаток=0, ОстатокRUR=0)])
        data = manager_detail_report(rows)
        summary = data['period_summary']
        self.assertEqual([column['label'] for column in summary['columns']], ['1 кв.', '2 кв.', '3 кв.', '4 кв.', 'Год'])
        self.assertEqual(summary['rows'][0]['values'], [Decimal(90), Decimal(0), Decimal(-10), None, Decimal(90)])
        for row, card in zip(summary['rows'], data['cards']):
            self.assertEqual(row['total'], card['total'])
            self.assertEqual(row['remainder'], card['bonus'])
            self.assertEqual(sum(value for value in row['values'] if value is not None) + row['remainder'], row['total'])
        extra = dict(rows[0], Год=2025, КварталМотивации='Другой период')
        multiple = period_motivation_summary([*rows, extra])
        self.assertIn('2025 · Другой период', [column['label'] for column in multiple['columns']])
        self.assertIn('2026 · 1 кв.', [column['label'] for column in multiple['columns']])
        self.assertEqual(multiple['rows'][0]['total'], summary['rows'][0]['total'] + Decimal(95))
        empty = period_motivation_summary([])
        self.assertEqual(empty['totals']['values'], [None] * 5)
        self.assertEqual(empty['totals']['total'], Decimal(0))

    def test_branch_execution_aligns_periods_and_uses_annual_values_in_total(self):
        rows = detail_rows()
        performance = [dict(row) for row in rows]
        performance[0].update(СуммаПланUSD=100, СуммаОтгрузокUSD=92,
                              СуммаПланUSDКвартал=100, СуммаОтгрузокUSDКвартал=93)
        performance[1].update(СуммаПланUSD=1000, СуммаОтгрузокUSD=790,
                              СуммаПланUSDКвартал=1000, СуммаОтгрузокUSDКвартал=800)
        performance.append(dict(performance[0], Год=2025, СуммаОтгрузокUSD=0))
        data = manager_detail_report(rows, performance_rows=performance)
        self.assertEqual(len(data['division_execution']), len(data['detail_totals']))
        self.assertEqual([block[16] for block in data['division_execution']], [Decimal('.92'), Decimal('.79'), Decimal('.79')])
        self.assertEqual(data['division_execution'][0][10:13], [Decimal(100), Decimal(93), Decimal('.93')])
        self.assertEqual(data['division_execution'][0][14:17], [Decimal(100), Decimal(92), Decimal('.92')])
        self.assertTrue(all(value is None for index, value in enumerate(data['division_execution'][0]) if index not in (10, 11, 12, 14, 15, 16)))
        with patch('admin_motivation.report_views.choices', return_value=[]), \
             patch('admin_motivation.report_views.load_report', return_value=data):
            response = self.client.get(reverse('motivation_report', args=['manager-detail']), {'run': '1', 'type': 'Продажи'})
        self.assertContains(response, '% выполнения подразделений')
        self.assertNotContains(response, 'Выполнение плана подразделений</h2>')
        self.assertLess(response.content.index(b'manager-division-execution'), response.content.index(b'report-total-row', response.content.index(b'manager-division-execution')))

    def test_weighted_averages_zero_denominators_and_separate_years(self):
        rows = detail_rows()
        rows[1]['K0WeightedSum'], rows[1]['K0Weight'] = Decimal('27'), 300
        self.assertEqual(metrics(rows, coefficients=True)[0], Decimal('.07125'))
        self.assertIsNone(metrics([{}], coefficients=True)[0])
        self.assertIsNone(metrics([{}])[12])
        self.assertEqual(metrics([{'СуммаОтгрузокUSDКвартал': 100}])[12], Decimal(1))
        rows[1]['Год'] = 2025
        self.assertEqual(manager_detail_report(rows)['periods'], ['2026 · 1 Квартал', '2025 · Год'])
        rows[1]['Подразделение'] = 'B'
        self.assertEqual(len(manager_detail_report(rows)['nodes']), 2)
        self.assertEqual(manager_detail_report([])['nodes'], [])

    def test_parameters_and_sql_group_before_fetch_without_interpolation(self):
        attack = "x'); DROP TABLE x; --"
        values = parameters('manager-detail', QueryDict('type=Директора&subdivision=A&subdivision=B&author=X&author=Y&year=2026&currency=RUB'))
        self.assertEqual(values['Subdivision'], ['A', 'B'])
        self.assertEqual(values['DealAuthor'], ['X', 'Y'])
        self.assertEqual(values['Years'], ['2026'])
        self.assertEqual(values['Currency'], 'RUB')
        with self.assertRaises(ValidationError): parameters('manager-detail', QueryDict('currency=bad'))
        with self.assertRaises(ValidationError): load_report('manager-detail', {})
        connection, cursor = Mock(), Mock()
        values['DealAuthor'] = [attack]
        with patch('admin_motivation.services.reports.connect_database', return_value=(connection, cursor)), \
             patch('admin_motivation.services.reports.fetch', return_value=detail_rows()) as fetch:
            result = load_report('manager-detail', values)
        sql, args = fetch.call_args_list[0].args[1:3]
        self.assertIn('GROUP BY', sql)
        self.assertIn('SUM([K0] * [O0])', sql)
        self.assertIn('CASE WHEN [K0] IS NOT NULL THEN [O0]', sql)
        self.assertIn('SUM([СуммаМотивацииRUR])', sql)
        self.assertNotIn(attack, sql)
        self.assertIn(attack, args)
        performance_sql, performance_args = fetch.call_args_list[1].args[1:3]
        self.assertNotIn('[АвторСделки]', performance_sql)
        self.assertNotIn(attack, performance_args)
        self.assertEqual(len(result['division_performance']), 2)
        self.assertEqual(result['currency'], 'RUB')
        connection.close.assert_called_once()

    def test_catalog_and_page_render_separately_from_short_report(self):
        self.assertContains(self.client.get(reverse('motivation_reports')), 'Мотивация. Детально')
        with patch('admin_motivation.report_views.choices', return_value=[]), \
             patch('admin_motivation.report_views.load_report', return_value=manager_detail_report(detail_rows())):
            page = self.client.get(reverse('motivation_report', args=['manager-detail']), {'run': '1', 'type': 'Продажи'})
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'Мотивация. Детально · 2026 · USD')
        self.assertContains(page, 'manager-detail-tree')
        self.assertContains(page, '190')
        self.assertNotContains(page, 'NoReverseMatch')
        self.client.logout()
        self.assertEqual(self.client.get(reverse('motivation_report', args=['manager-detail'])).status_code, 302)

    def test_global_subdivision_uses_department_execution_at_every_level(self):
        from .services.pilot_formula import GLOBAL_SUBDIVISION
        rows = [dict(row, VariationCalculate=GLOBAL_SUBDIVISION) for row in detail_rows()]
        performance = [dict(row, СуммаПланUSD=1000, СуммаОтгрузокUSD=Decimal('628.4')) for row in rows]
        performance.append(dict(performance[0], Подразделение='Other', СуммаПланUSD=1, СуммаОтгрузокUSD=999))
        data = manager_detail_report(rows, performance_rows=performance)
        def check(blocks):
            for block in blocks:
                self.assertIsNone(block[10])
                self.assertIsNone(block[12])
                self.assertEqual(block[14:17], [Decimal(1000), Decimal('628.4'), Decimal('.6284')])
        check(data['detail_totals'])
        def walk(nodes):
            for node in nodes:
                check(node['values'])
                walk(node['children'])
        walk(data['nodes'])
        self.assertEqual(data['detail_totals'][0][11], metrics([rows[0]])[11])
        self.assertEqual(data['detail_totals'][0][13], metrics([rows[0]])[13])
        self.assertEqual(data['detail_totals'][0][17:], metrics([rows[0]])[17:])
        self.assertEqual(data['division_execution'][0][16], Decimal('.6284'))
        self.assertEqual(data['division_execution'][0][10:13], metrics([performance[0]])[10:13])
        self.assertEqual(data['division_execution'][0][14:17], [Decimal(1000), Decimal('628.4'), Decimal('.6284')])

    def test_global_execution_is_not_summed_again_for_authors_or_products(self):
        from .services.pilot_formula import GLOBAL_SUBDIVISION
        rows = [dict(row, VariationCalculate=GLOBAL_SUBDIVISION) for row in detail_rows()]
        extra = [dict(row, АвторСделки='Петров', ГруппаПлановПродаж='Other product') for row in rows]
        performance = [dict(row, СуммаПланUSD=1000, СуммаОтгрузокUSD=600) for row in rows]
        data = manager_detail_report(rows + extra, performance_rows=performance)
        self.assertEqual(data['detail_totals'][0][14:17], [1000, 600, Decimal('.6')])
        self.assertEqual(data['detail_totals'][0][11], metrics([rows[0], extra[0]])[11])
        mixed = manager_detail_report(rows + [dict(extra[0], VariationCalculate='Группа планов продаж')], performance_rows=performance)
        self.assertEqual(mixed['detail_totals'][0][14:17], [None, None, None])

    def test_global_product_without_own_period_still_has_department_execution_and_no_personal_plan(self):
        from .services.pilot_formula import GLOBAL_SUBDIVISION
        rows = [dict(row, VariationCalculate=GLOBAL_SUBDIVISION) for row in detail_rows()]
        annual_only = dict(rows[1], Марка='Annual-only brand')
        performance = [dict(row, СуммаПланUSD=1000, СуммаОтгрузокUSD=600) for row in rows]
        report = manager_detail_report(rows + [annual_only], performance_rows=performance)
        def find(nodes):
            for node in nodes:
                if node['label'] == 'Annual-only brand':
                    return node
                found = find(node['children'])
                if found:
                    return found
        node = find(report['nodes'])
        self.assertIsNotNone(node)
        quarter = node['values'][0]
        self.assertIsNone(quarter[10])
        self.assertIsNone(quarter[12])
        self.assertEqual(quarter[11], 0)
        self.assertEqual(quarter[14:17], [1000, 600, Decimal('.6')])
