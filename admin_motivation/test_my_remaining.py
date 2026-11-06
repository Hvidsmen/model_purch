from copy import deepcopy
from decimal import Decimal
from unittest.mock import patch

from django.http import QueryDict
from django.template.loader import render_to_string
from django.test import SimpleTestCase

from .services.reports_next_presenter import present
from .test_performance_matrix import performance


class MyRemainingTests(SimpleTestCase):
    def test_all_coefficient_cards_merge_q4_and_year_at_report_precision(self):
        kinds = ['Политики', 'Продажи', 'Директора']
        rows = [{'subdivision': 'Д-Москва', 'author': 'Сотрудник', 'unallocated': False,
                 'kind': kind, 'code': code, 'period': label,
                 'amount': Decimal(0), 'bonus': Decimal(0), 'total': Decimal(0)}
                for kind in kinds for code, label in [('4', '4 Квартал'), ('5', 'Год')]]
        metrics = [performance(period, kind=kind, plan=Decimal('13047248.67000001') + epsilon,
                               fact=Decimal('3275155.60000001') + epsilon,
                               ratio=Decimal('.251023440001') + epsilon / Decimal(1000000))
                   for kind in kinds for period, epsilon in [('4 Квартал', Decimal(0)), ('Год', Decimal('.00000001'))]]
        ledger = {'rows': rows, 'total': Decimal(0), 'kinds': kinds, 'plan_fact': metrics}
        original = deepcopy(ledger)
        result = present('team', ledger, ledger, ['4', '5'])
        self.assertEqual(ledger, original)
        for section in result['team_sections']:
            row = section['remaining_targets'][0]
            self.assertEqual(len(row['targets']), 2)
            self.assertEqual(len(row['display_targets']), 1)
            self.assertEqual(row['display_targets'][0]['label'], 'IV квартал и годовая часть')
        html = render_to_string('admin_motivation/reports_next/_team_remaining.html', {'presentation': result})
        self.assertEqual(html.count('IV квартал и годовая часть'), 6)  # badge and accessible progress label
        metrics[1]['fact'] += Decimal(1)
        changed = present('team', ledger, ledger, ['4', '5'])
        self.assertEqual(len(changed['team_sections'][0]['remaining_targets'][0]['display_targets']), 2)

    def test_identical_q4_and_year_share_one_display_card_without_changing_targets(self):
        rows = [{'subdivision': 'Д-Москва', 'author': 'Сотрудник', 'unallocated': False,
                 'kind': 'Политики', 'code': code, 'period': label,
                 'amount': Decimal(0), 'bonus': Decimal(0), 'total': Decimal(0)}
                for code, label in [('4', '4 Квартал'), ('5', 'Год')]]
        ledger = {'rows': rows, 'total': Decimal(0), 'kinds': ['Политики'],
                  'department_plan_fact': [performance(period, kind='Всё подразделение')
                                           for period in ('4 Квартал', 'Год')]}
        result = present('team', ledger, ledger, ['4', '5'])
        row = result['department_remaining_targets'][0]
        self.assertEqual(len(row['targets']), 2)
        self.assertEqual(len(row['display_targets']), 1)
        self.assertEqual(row['display_targets'][0]['label'], 'IV квартал и годовая часть')
        self.assertEqual(row['display_targets'][0]['progress'], Decimal(66))
        ledger['department_plan_fact'][1]['fact'] = Decimal(90)
        different = present('team', ledger, ledger, ['4', '5'])
        self.assertEqual(len(different['department_remaining_targets'][0]['display_targets']), 2)

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
        html = render_to_string('admin_motivation/reports_next/_my_remaining.html', {'targets': result['remaining_targets']})
        self.assertIn('Осталось до 100%', html)
        self.assertIn('80,00 USD', html)
        self.assertIn('30,00 USD', html)
        self.assertIn('Директора', html)
        self.assertIn('— USD', html)
        overview = render_to_string('admin_motivation/reports_next/_team_remaining.html', {'presentation': result})
        self.assertIn('nr-kpis nr-target-overview', overview)
        self.assertEqual(overview.count('nr-target-column'), 3)
        self.assertIn('Политики', overview)
        self.assertIn('Директора', overview)
        self.assertNotIn('Продажи', overview)
        self.assertEqual(overview.count('nr-target-secondary'), 3)
        self.assertEqual(len(result['department_remaining_targets']), 1)
        for section in result['team_sections']:
            self.assertTrue(all(row['kind'] == section['kind'] for row in section['remaining_targets']))
        director = next(section for section in result['team_sections'] if section['kind'] == 'Директора')
        panel = render_to_string('admin_motivation/reports_next/_type_performance.html',
                                 {'kind': 'team', 'section': director, 'presentation': result})
        self.assertIn('Осталось до 100%', panel)
        self.assertNotIn('Политики', panel)
        from django.template.loader import get_template
        source = get_template('admin_motivation/reports_next/report.html').template.source
        self.assertIn("{% if kind == 'team' %}{% include 'admin_motivation/reports_next/_team_remaining.html'", source)
        self.assertNotIn("{% elif kind == 'mine' %}\n{% include 'admin_motivation/reports_next/_my_remaining.html'", source)

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



