from decimal import Decimal
from unittest.mock import Mock, patch

from django.http import QueryDict
from django.template.loader import render_to_string
from django.test import SimpleTestCase
from openpyxl import load_workbook

from .services.annual_accrual import grouped_sql, lookup
from .services.presentation_excel import workbook
from .services.reports_next_presenter import present
from .test_quarter_totals import premium


class AnnualAccrualTests(SimpleTestCase):
    def ledger(self):
        rows = []
        for kind, multiplier in [('Политики', 1), ('Продажи', 2), ('Директора', 3)]:
            for code, paid, annual in [('1', '40', '13'), ('2', '80', '17'), ('3', '0', '19'), ('5', '0', None)]:
                row = premium(code, kind, str(Decimal(paid) * multiplier))
                row['annual_accrual'] = Decimal(annual) * multiplier if annual is not None else None
                row['currency'] = 'USD'
                rows.append(row)
        return {'rows': rows, 'total': sum(row['amount'] for row in rows),
                'kinds': ['Политики', 'Продажи', 'Директора'], 'currency': 'USD'}

    def test_annual_source_uses_year_share_and_original_shipment_quarter(self):
        usd = grouped_sql('SELECT * FROM scoped_source')
        rub = grouped_sql('SELECT * FROM scoped_source', 'RUB')
        self.assertIn("= N'год'", usd)
        self.assertIn('[Квартал] IN (1,2,3,4)', usd)
        self.assertIn('SUM([СуммаМотивацииUSD])', usd)
        self.assertIn('SUM([СуммаМотивацииRUR])', rub)
        self.assertNotIn('УчетПроцента', usd)
        self.assertNotIn('MAX(', usd)
        self.assertNotIn('0.2', usd)

    def test_lookup_preserves_zero_and_unallocated_author(self):
        rows = [{'Подразделение': 'Д-Москва', 'АвторСделки': None, 'Квартал': 3, 'AnnualAccrual': 0},
                {'Подразделение': 'Д-Москва', 'АвторСделки': 'Иван', 'Квартал': 2, 'AnnualAccrual': Decimal('13.42')}]
        result = lookup(rows)
        self.assertEqual(result['Д-Москва', '—', '3'], 0)
        self.assertEqual(result['Д-Москва', 'Иван', '2'], Decimal('13.42'))

    def test_cards_types_employees_and_footer_reconcile_without_year_double_count(self):
        ledger = self.ledger()
        result = present('registry', ledger, ledger, ['1', '2', '3', '5'])
        self.assertEqual(result['annual_accrual'], Decimal(294))
        self.assertEqual(result['total'], Decimal(720))
        self.assertEqual([card['annual_accrual'] for card in result['period_cards']], [78, 102, 114, None])
        self.assertEqual(sum(row['annual_accrual'] for row in result['registry_rows']), result['annual_accrual'])
        for index, card in enumerate(result['period_cards'][:3]):
            self.assertEqual(sum(item['annual_accrual'] for item in card['type_totals']), card['annual_accrual'])
            for item, section in zip(card['type_totals'], result['team_sections']):
                self.assertEqual(item['annual_accrual'], section['period_summary_cells'][index]['annual_accrual'])
                self.assertEqual(item['annual_accrual'], sum(row['period_cells'][index]['annual_accrual'] for row in section['rows']))
        self.assertEqual(result['period_cards'][2]['total'], 0)
        self.assertGreater(result['period_cards'][2]['annual_accrual'], 0)

    def test_selected_quarter_and_type_only(self):
        ledger = self.ledger()
        ledger['rows'] = [row for row in ledger['rows'] if row['code'] == '2' and row['kind'] == 'Продажи']
        ledger['kinds'] = ['Продажи']
        result = present('registry', ledger, ledger, ['2'])
        self.assertEqual(result['annual_accrual'], 34)
        self.assertEqual(len(result['period_cards']), 1)
        self.assertEqual(result['type_totals'][0]['annual_accrual'], 34)

    def test_missing_is_unknown_and_zero_is_visible(self):
        ledger = self.ledger()
        ledger['rows'][0]['annual_accrual'] = None
        result = present('registry', ledger, ledger)
        self.assertIsNone(result['annual_accrual'])
        html = render_to_string('admin_motivation/reports_next/_annual_accrual.html', {'amount': Decimal(0)})
        self.assertIn('0,00', html)
        html = render_to_string('admin_motivation/reports_next/_annual_accrual.html', {'amount': None})
        self.assertNotIn('В годовую', html)

    def test_cards_and_employee_cells_show_annual_separately_from_paid(self):
        ledger = self.ledger()
        result = present('registry', ledger, ledger)
        html = render_to_string('admin_motivation/reports_next/_quarter_totals.html', {'presentation': result, 'ledger': ledger})
        self.assertEqual(html.count('В годовую премию за квартал'), 3)
        self.assertIn('294,00 USD', html)
        self.assertIn('До проверки годового условия выплаты', html)
        html = render_to_string('admin_motivation/reports_next/_employee_period_cells.html',
                                {'row': result['registry_rows'][0], 'show_annual_accrual': True})
        self.assertEqual(html.count('В годовую премию:'), 3)
        from django.core.paginator import Paginator
        for section in result['team_sections']:
            section['page'] = Paginator(section['rows'], 100).get_page(1)
        html = render_to_string('admin_motivation/reports_next/_all_2026_tables.html',
                                {'presentation': result, 'ledger': ledger})
        self.assertEqual(html.count('В годовую премию:'), 24)

    @patch('admin_motivation.reports_all_2026.reverse', return_value='/motivation/all-2026/')
    @patch('admin_motivation.reports_all_2026.reports_next.load_report')
    @patch('admin_motivation.reports_all_2026.selection', return_value=(None, ['Политики'], ['3']))
    def test_data_for_attaches_original_quarter_share_to_zero_payout(self, selection, load, reverse):
        from .reports_all_2026 import data_for
        load.return_value = {'period_summary': {'columns': [{'label': '3 кв.'}],
            'rows': [{'subdivision': 'Д-Москва', 'author': 'Иван', 'values': [Decimal(0)], 'possible_values': [Decimal(50)]}]},
            'annual_accrual': [{'Подразделение': 'Д-Москва', 'АвторСделки': 'Иван', 'Квартал': 3, 'AnnualAccrual': Decimal('17.34')},
                               {'Подразделение': 'Д-Москва', 'АвторСделки': 'Иван', 'Квартал': 2, 'AnnualAccrual': Decimal(100)}]}
        result = data_for(Mock(), Mock(), QueryDict('period=3&type=Политики'))
        self.assertEqual(len(result['rows']), 1)
        self.assertEqual(result['rows'][0]['annual_accrual'], Decimal('17.34'))
        self.assertEqual(result['rows'][0]['amount'], 0)
        self.assertEqual(result['total'], 0)
        self.assertEqual(load.call_args.kwargs['premium_period'], ['3'])

    def test_excel_annual_sheet_reconciles_and_preserves_payout_sheet(self):
        ledger = self.ledger()
        output = workbook('registry', ledger, [], query=QueryDict('period=1&period=2&period=3&period=5'))
        try:
            book = load_workbook(output, data_only=True)
            annual = list(book['В годовую по кварталам'].values)
            self.assertEqual(annual[-1][3:7], (78, 102, 114, 294))
            self.assertEqual(len(annual[4]), 8)  # quarters 1..3, no duplicate year column
            summary = list(book['Сводка'].values)
            self.assertEqual(summary[8][7], 720)
            book.close()
        finally:
            output.close()

    @patch('admin_motivation.services.subdivision_aliases.rules', return_value=[])
    @patch('admin_motivation.services.subdivision_scope.sql_predicate', return_value='1=1')
    @patch('admin_motivation.services.reports.connect_database')
    @patch('admin_motivation.services.reports.fetch')
    def test_loader_reads_year_rows_even_when_only_quarter_three_is_selected(self, fetch, connect, predicate, aliases):
        from .services.reports import load_report
        connection, cursor = Mock(), Mock()
        connect.return_value = connection, cursor
        fetch.return_value = []
        values = {'TypeCoeff_': 'Политики', 'Subdivision': ['Д-Москва'], 'DealAuthor': ['Иван'],
                  'Years': ['2026'], 'Currency': 'RUB', 'gp_sales': [], 'GPGoods': [], 'Brend': []}
        result = load_report('manager-detail', values, premium_period=['3'], summary_only=True, calculation_variant='all_2026')
        annual = fetch.call_args_list[-1].args
        self.assertIn('ReportMotivationByDealAuthorAll2026', annual[1])
        self.assertIn("= N'год'", annual[1])
        self.assertNotIn("LIKE N'3 кв%'", annual[1])
        self.assertIn('Д-Москва', annual[2])
        self.assertIn('Иван', annual[2])
        self.assertIn('Политики', annual[2])
        self.assertIn('2026', annual[2])
        self.assertIn('SUM([СуммаМотивацииRUR])', annual[1])
        self.assertEqual(result['annual_accrual'], [])
        connection.close.assert_called_once()

    @patch('admin_motivation.services.active_directors.report_roster', return_value='<directors/>')
    @patch('admin_motivation.services.subdivision_aliases.rules', return_value=[])
    @patch('admin_motivation.services.subdivision_scope.sql_predicate', return_value='1=1')
    @patch('admin_motivation.services.reports.connect_database')
    @patch('admin_motivation.services.reports.fetch', return_value=[])
    def test_annual_source_keeps_current_director_filter(self, fetch, connect, predicate, aliases, roster):
        from .services.reports import load_report
        connect.return_value = Mock(), Mock()
        load_report('manager-detail', {'TypeCoeff_': 'Директора', 'Years': ['2026'], 'Currency': 'USD',
                    'Subdivision': [], 'DealAuthor': [], 'gp_sales': [], 'GPGoods': [], 'Brend': []},
                    premium_period=['1'], summary_only=True, calculation_variant='all_2026')
        annual = fetch.call_args_list[-1].args
        self.assertIn('<directors/>', annual[2])
        self.assertIn('.nodes(', annual[1])
        self.assertNotIn("LIKE N'1 кв%'", annual[1])
