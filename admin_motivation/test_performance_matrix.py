from copy import deepcopy
from decimal import Decimal

from django.template.loader import render_to_string
from django.test import SimpleTestCase

from .services.reports_next_presenter import present, performance_matrix


def performance(period, **changes):
    return {'subdivision': 'Д-Москва', 'year': 2026, 'kind': 'Политики',
            'formula': 'Подразделение(Глобально)', 'scope_author': 'Всё подразделение',
            'period': period, 'plan': Decimal(100), 'fact': Decimal(66), 'ratio': Decimal('.66'), **changes}


class PerformanceMatrixTests(SimpleTestCase):
    def test_plan_fact_columns_follow_employee_periods_and_leave_missing_cells_empty(self):
        rows = []
        for code in ['3', '1', '5']:
            rows.append({'subdivision': 'Д-Москва', 'author': 'Сотрудник', 'unallocated': False,
                         'kind': 'Политики', 'code': code, 'period': 'Год' if code == '5' else code + ' Квартал',
                         'amount': Decimal(10), 'bonus': Decimal(0), 'total': Decimal(10), 'possible': Decimal(20)})
        ledger = {'rows': rows, 'total': Decimal(30), 'kinds': ['Политики'],
                  'plan_fact': [performance('3 Квартал'), performance('1 Квартал', fact=Decimal(100))]}
        original = deepcopy(ledger)
        presentation = present('registry', ledger, ledger, ['1', '3', '5'])
        matrix = presentation['performance_matrix']
        self.assertEqual([column['code'] for column in matrix['columns']],
                         [column['code'] for column in presentation['period_columns']])
        self.assertEqual([column['code'] for column in matrix['columns']], ['1', '3', '5'])
        self.assertEqual([cell['period'] if cell else None for cell in matrix['rows'][0]['cells']],
                         ['1 Квартал', '3 Квартал', None])
        self.assertEqual(ledger, original)
        self.assertEqual(presentation['total'], Decimal(30))
        self.assertEqual(presentation['team_sections'][0]['period_totals'], [Decimal(10)] * 3)
        html = render_to_string('admin_motivation/reports_next/_performance_matrix.html', {'presentation': presentation})
        self.assertLess(html.index('data-period="1"'), html.index('data-period="3"'))
        self.assertLess(html.index('data-period="3"'), html.index('data-period="5"'))
        self.assertNotIn('data-period="2"', html)
        self.assertIn('66,00%', html)
        self.assertIn('—', html)

    def test_different_bases_stay_separate_and_unselected_periods_are_omitted(self):
        items = [performance('3 Квартал'), performance('Год', plan=Decimal(999)),
                 performance('3 Квартал', kind='Продажи'),
                 performance('3 Квартал', formula='Подразделение', scope_author='Сотрудник')]
        matrix = performance_matrix(items, [{'code': '3'}])
        self.assertEqual(len(matrix['rows']), 3)
        self.assertTrue(all(len(row['cells']) == 1 and row['cells'][0]['plan'] == 100 for row in matrix['rows']))

    def test_duplicate_source_periods_are_preserved_without_summing_or_overwriting(self):
        matrix = performance_matrix([performance('3 Квартал'), performance('3 Квартал', plan=Decimal(200))], [{'code': '3'}])
        self.assertEqual([row['cells'][0]['plan'] for row in matrix['rows']], [100, 200])
