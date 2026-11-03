from decimal import Decimal

from django.template.loader import render_to_string
from django.test import SimpleTestCase

from .services.reports_next_presenter import present


def premium(code, kind, amount, author='Сотрудник', branch='Д-Москва'):
    amount = Decimal(amount)
    return {'subdivision': branch, 'author': author, 'unallocated': False,
            'kind': kind, 'code': code, 'period': 'Год' if code == '5' else code + ' Квартал',
            'amount': amount, 'bonus': Decimal(0), 'total': amount, 'possible': amount}


class QuarterTotalTests(SimpleTestCase):
    def test_quarter_and_year_totals_reconcile_with_employee_tables_and_types(self):
        kinds = ['Политики', 'Продажи', 'Директора']
        rows = [premium('3', 'Политики', '0'), premium('1', 'Политики', '10.25'),
                premium('1', 'Политики', '5.75', author='Другой', branch='Д-СПб'),
                premium('1', 'Продажи', '20'), premium('1', 'Директора', '30'),
                premium('3', 'Продажи', '0'), premium('3', 'Директора', '0'),
                premium('5', 'Политики', '7'), premium('5', 'Продажи', '8'), premium('5', 'Директора', '9')]
        ledger = {'rows': rows, 'total': Decimal(90), 'kinds': kinds, 'currency': 'RUB'}
        result = present('registry', ledger, ledger, ['1', '3', '5'])
        cards = result['period_cards']
        self.assertEqual([card['code'] for card in cards], ['1', '3', '5'])
        self.assertEqual([card['total'] for card in cards], [Decimal(66), Decimal(0), Decimal(24)])
        for index, card in enumerate(cards):
            self.assertEqual(card['total'], sum(item['total'] for item in card['type_totals']))
            self.assertEqual(card['total'], result['period_totals'][index])
            for item, section in zip(card['type_totals'], result['team_sections']):
                self.assertEqual(item['kind'], section['kind'])
                self.assertEqual(item['total'], section['period_totals'][index])
        html = render_to_string('admin_motivation/reports_next/_quarter_totals.html', {'presentation': result, 'ledger': ledger})
        self.assertEqual(html.count('<article'), 3)
        self.assertIn('66,00 RUB', html)
        self.assertIn('0,00 RUB', html)
        self.assertIn('Годовая премия', html)
        self.assertNotIn('USD', html)

    def test_filtered_type_does_not_invent_other_types_or_periods(self):
        ledger = {'rows': [premium('2', 'Политики', '12.50')], 'total': Decimal('12.50'),
                  'kinds': ['Политики'], 'currency': 'USD'}
        result = present('registry', ledger, ledger, ['2'])
        self.assertEqual(len(result['period_cards']), 1)
        self.assertEqual(result['period_cards'][0]['type_totals'], [{'kind': 'Политики', 'total': Decimal('12.50')}])
        html = render_to_string('admin_motivation/reports_next/_quarter_totals.html', {'presentation': result, 'ledger': ledger})
        self.assertNotIn('Директора', html)
        self.assertNotIn('Продажи', html)

    def test_no_rows_hides_cards_instead_of_displaying_fabricated_zero_totals(self):
        ledger = {'rows': [], 'total': Decimal(0), 'kinds': ['Политики']}
        result = present('registry', ledger, ledger)
        html = render_to_string('admin_motivation/reports_next/_quarter_totals.html', {'presentation': result, 'ledger': ledger})
        self.assertEqual(result['period_cards'], [])
        self.assertNotIn('<article', html)
