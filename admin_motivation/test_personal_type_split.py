from decimal import Decimal
from django.template.loader import render_to_string
from django.test import SimpleTestCase
from .services.reports_next_presenter import present
from .test_quarter_totals import premium


class PersonalTypeSplitTests(SimpleTestCase):
    def render(self, rows, kinds=None):
        for row in rows:
            row['detail_url'] = '/detail/?type=' + row['kind'] + '&period=' + row['code']
        ledger = {'rows': rows, 'kinds': kinds or list(dict.fromkeys(row['kind'] for row in rows)),
                  'currency': 'USD', 'total': sum((row['total'] for row in rows), Decimal(0))}
        display = present('mine', ledger, ledger, ['4', '5'])
        html = render_to_string('admin_motivation/reports_next/_my_periods.html',
                                {'presentation': display, 'ledger': ledger})
        return display, html

    def test_policies_and_sales_are_visible_in_existing_period_and_total_cards(self):
        rows = [premium('4', 'Политики', '10'), premium('5', 'Политики', '5'),
                premium('4', 'Продажи', '20'), premium('5', 'Продажи', '7')]
        display, html = self.render(rows)
        self.assertEqual(display['total'], 42)
        self.assertEqual([card['amount'] for card in display['period_cards']], [30, 12])
        self.assertEqual(html.count('<article'), 3)  # Existing two periods and total.
        for amount in ('10,00', '5,00', '20,00', '7,00', '15,00', '27,00', '42,00'):
            self.assertIn(amount + ' USD', html)
        self.assertEqual(html.count('<span>Политики</span>'), 3)
        self.assertEqual(html.count('<span>Продажи</span>'), 3)
        self.assertIn('type=Политики&amp;period=4', html)
        self.assertIn('type=Продажи&amp;period=5', html)
        self.assertIn('Почему премия равна нулю', self.zero_explanation())
        self.assertNotIn('role="tablist"', html)
        self.assertNotIn('<table', html)

    def zero_explanation(self):
        row = premium('4', 'Политики', '0')
        ledger = {'rows': [row], 'kinds': ['Политики'], 'total': Decimal(0), 'currency': 'USD'}
        display = present('mine', ledger, ledger)
        display['period_cards'][0]['explanation'] = 'Порог выплаты не достигнут.'
        return render_to_string('admin_motivation/reports_next/_my_periods.html', {'presentation': display, 'ledger': ledger})

    def test_director_and_politician_parts_remain_separate_for_same_person(self):
        display, html = self.render([premium('4', 'Политики', '1000'), premium('4', 'Директора', '2000')])
        self.assertEqual(display['people'], 1)
        self.assertEqual(display['total'], 3000)
        self.assertIn('<span>Директора</span>', html)
        self.assertIn('1 000,00 USD', html.replace('\xa0', ' '))
        self.assertIn('2 000,00 USD', html.replace('\xa0', ' '))
        self.assertEqual(html.count('<article'), 2)

    def test_zero_part_is_visible_but_absent_type_is_not_added(self):
        display, html = self.render([premium('4', 'Политики', '0'), premium('4', 'Продажи', '20')],
                                    kinds=['Политики', 'Продажи', 'Директора'])
        self.assertIn('<span>Политики</span><b>0,00 USD</b>', html)
        self.assertNotIn('<span>Директора</span>', html)
        self.assertEqual(display['total'], 20)

    def test_one_allowed_type_does_not_expose_other_types(self):
        _, html = self.render([premium('4', 'Политики', '11')])
        self.assertIn('<span>Политики</span>', html)
        self.assertNotIn('Продажи', html)
        self.assertNotIn('Директора', html)
