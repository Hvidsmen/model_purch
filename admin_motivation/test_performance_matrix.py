from copy import deepcopy
from decimal import Decimal
from html.parser import HTMLParser

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

    def test_types_keep_separate_rows_even_when_their_global_bases_match(self):
        items = [performance(period, kind=kind) for kind in ['Политики', 'Продажи', 'Директора']
                 for period in ['1 Квартал', '3 Квартал']]
        matrix = performance_matrix(items, [{'code': '1'}, {'code': '3'}])
        self.assertEqual(len(matrix['rows']), 3)
        self.assertEqual([row['kind'] for row in matrix['rows']], ['Политики', 'Продажи', 'Директора'])
        self.assertEqual([cell['plan'] for cell in matrix['rows'][0]['cells']], [100, 100])

    def test_float_noise_and_identical_source_duplicates_do_not_repeat_display_rows(self):
        items = [performance('3 Квартал', plan=Decimal('14430562.93000001'), fact=Decimal('9530330.00000001')),
                 performance('3 Квартал', kind='Продажи', plan=Decimal('14430562.92999999'), fact=Decimal('9530329.99999999'), ratio=Decimal('.6600000000001')),
                 performance('3 Квартал', kind='Директора', plan=Decimal('14430562.93'), fact=Decimal('9530330'))]
        items += items[:1]
        matrix = performance_matrix(items, [{'code':'3'}])
        self.assertEqual(len(matrix['rows']), 3)
        total = performance('Итого', plan=Decimal(300), fact=Decimal(150), ratio=Decimal('.5'))
        matrix = performance_matrix([*items,total], [{'code':'3'}])
        self.assertEqual(matrix['rows'][0]['total'], total)
        html = render_to_string('admin_motivation/reports_next/_performance_matrix.html', {'presentation': {'period_columns':[{'code':'3'}], 'performance_matrix':matrix}})
        self.assertIn('Итого', html)
        self.assertIn('50,00%', html)

    def test_plan_fact_and_employee_tables_share_exact_period_column_boundaries(self):
        class Columns(HTMLParser):
            def __init__(self):
                super().__init__()
                self.tables = []

            def handle_starttag(self, tag, attrs):
                attrs = dict(attrs)
                if tag == 'table':
                    self.tables.append([])
                if tag == 'col':
                    self.tables[-1].append(int(attrs['style'].removeprefix('width:').removesuffix('px')))

        for codes in (['1', '2', '3'], ['3'], ['1', '5']):
            columns = [{'code': code, 'label': code} for code in codes]
            matrix = performance_matrix([performance(code + ' Квартал') for code in codes], columns)
            presentation = {'period_columns': columns, 'performance_matrix': matrix,
                            'team_sections': [{'kind': 'Политики', 'people': 0, 'page': [], 'period_totals': []}]}
            context = {'presentation': presentation, 'ledger': {'currency': 'USD'}}
            parser = Columns()
            for name in ['_performance_matrix.html', '_all_2026_tables.html']:
                parser.feed(render_to_string('admin_motivation/reports_next/' + name, context))
            upper, lower = parser.tables
            self.assertEqual(sum(upper), sum(lower))
            for index in range(len(codes)):
                self.assertEqual(sum(upper[:1 + 3 * index]), sum(lower[:1 + index]))
                self.assertEqual(sum(upper[:1 + 3 * (index + 1)]), sum(lower[:2 + index]))
