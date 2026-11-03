from decimal import Decimal
from django.http import QueryDict
from django.test import SimpleTestCase
from openpyxl import load_workbook
from .services.presentation_excel import workbook


class PresentationExcelTests(SimpleTestCase):
    def data(self):
        return {'currency': 'USD', 'kinds': ['Политики'], 'total': Decimal('10.25'),
                'rows': [{'subdivision': 'A', 'author': '=unsafe', 'kind': 'Политики',
                          'unallocated': False, 'period': '3 Квартал', 'code': '3',
                          'amount': Decimal('10.25'), 'bonus': Decimal(0), 'total': Decimal('10.25'),
                          'possible': Decimal('20.50'), 'currency': 'USD'}],
                'plan_fact': [{'subdivision': 'A', 'kind': 'Политики', 'period': '3 Квартал',
                               'plan': Decimal(100), 'fact': Decimal(75), 'ratio': Decimal('.75')}]}

    def test_workbook_layout_numbers_filters_and_injection_protection(self):
        output = workbook('registry', self.data(), [('Source', '=unsafe')], QueryDict('period=3'), title='Все кварталы 2026')
        book = load_workbook(output)
        try:
            self.assertEqual(book.sheetnames[0], 'Сводка')
            self.assertIn('Политики', book.sheetnames)
            self.assertIn('По периодам', book.sheetnames)
            self.assertEqual(book['Политики']['B6'].value, '=unsafe')
            self.assertEqual(book['Политики']['B6'].data_type, 's')
            self.assertEqual(book['Политики']['C6'].value, 10.25)
            self.assertEqual(book['Политики'].freeze_panes, 'C6')
            self.assertTrue(book['Политики'].auto_filter.ref.startswith('A5:'))
            self.assertFalse(book['Политики'].sheet_view.showGridLines)
            self.assertEqual(book['План-факт']['H6'].value, .75)
            self.assertEqual(book['План-факт']['F6'].number_format, '0.00%')
            self.assertEqual(book['Параметры']['B6'].data_type, 's')
            self.assertEqual(book['Сводка']['A1'].value, 'Все кварталы 2026')
        finally:
            book.close()
            output.close()
