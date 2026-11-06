from copy import deepcopy
from decimal import Decimal
from unittest.mock import patch

from django.http import QueryDict
from django.template.loader import render_to_string
from django.test import SimpleTestCase

from .services.reports_next_presenter import present
from .test_performance_matrix import performance


class MyRemainingTests(SimpleTestCase):
    def test_department_and_each_coefficient_keep_their_own_targets(self):
        rows = [{'subdivision': 'Д-Москва', 'author': 'Сотрудник', 'unallocated': False,
                 'kind': 'Политики', 'code': code, 'period': label,
                 'amount': Decimal(0), 'bonus': Decimal(0), 'total': Decimal(0)}
                for code, label in [('4', '4 Квартал'), ('5', 'Год')]]
        ledger = {'rows': rows, 'total': Decimal(0), 'kinds': ['Политики', 'Директора'],
                  'plan_fact': [performance('4 Квартал', plan=Decimal(100), fact=Decimal(70)),
                                performance('4 Квартал', kind='Директора', plan=Decimal(150), fact=Decimal(160)),
                                performance('Год', plan=None, fact=None)],
                  'department_plan_fact': [performance('4 Квартал', kind='Всё подразделение', plan=Decimal(200), fact=Decimal(120))]}
        original = deepcopy(ledger)
        result = present('mine', ledger, ledger, ['4', '5'])
        targets = result['remaining_targets']
        self.assertEqual([(row['title'], row['kind'], [cell['remaining'] for cell in row['targets']]) for row in targets],
                         [('Всё подразделение', 'Всё подразделение', [Decimal(80)]),
                          ('По коэффициентам', 'Политики', [Decimal(30), None]),
                          ('По коэффициентам', 'Директора', [Decimal(0)])])
        self.assertEqual(ledger, original)
        html = render_to_string('admin_motivation/reports_next/_my_remaining.html', {'presentation': result})
        self.assertIn('Осталось до 100%', html)
        self.assertIn('80,00 USD', html)
        self.assertIn('30,00 USD', html)
        self.assertIn('Директора', html)
        self.assertIn('— USD', html)

    @patch('admin_motivation.services.reports_next.restrict', return_value=(None, ['Политики'], '4'))
    def test_detailed_personal_report_requests_separate_performance_sources(self, restrict):
        from types import SimpleNamespace
        from .services.reports_next import actual_reports
        source = {'period_summary': {'columns': [], 'rows': []},
                  'motivation_performance': [], 'department_performance': []}
        with patch('admin_motivation.services.reports_next.load_report', return_value=source) as load:
            actual_reports(None, SimpleNamespace(parameters={'fact_year': 2026}), QueryDict('year=2026'), detailed=True)
        self.assertTrue(load.call_args.kwargs['include_performance'])
        self.assertFalse(load.call_args.kwargs['summary_only'])
