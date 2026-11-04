from decimal import Decimal
from django.core.paginator import Paginator
from django.template.loader import render_to_string
from django.test import SimpleTestCase
from .services.reports_next_presenter import present
from .test_quarter_totals import premium


class RegistryTypeTabTests(SimpleTestCase):
    def context(self, kinds=None):
        rows = [premium('4', 'Политики', '1', author=f'Политик {index}') for index in range(57)]
        rows += [premium('4', 'Продажи', '2', author='Продажи')]
        rows += [premium('4', 'Директора', '3', author=f'Директор {index}') for index in range(2)]
        kinds = kinds or ['Политики', 'Продажи', 'Директора']
        rows = [row for row in rows if row['kind'] in kinds]
        ledger = {'rows': rows, 'kinds': kinds, 'total': sum(row['total'] for row in rows), 'currency': 'USD'}
        display = present('registry', ledger, ledger)
        for section in display['team_sections']:
            section['page'] = Paginator(section['rows'], 20).get_page(2 if section['kind'] == 'Политики' else 1)
        return {'kind': 'registry', 'presentation': display, 'ledger': ledger, 'page_query': 'period=4'}

    def test_registry_has_same_type_tabs_with_full_employee_counts(self):
        context = self.context()
        html = render_to_string('admin_motivation/reports_next/_team_ledger.html', context)
        self.assertEqual(html.count('data-team-tabs'), 1)
        self.assertEqual(html.count('role="tab"'), 3)
        self.assertEqual(html.count('role="tabpanel"'), 3)
        for kind, count in [('Политики', 57), ('Продажи', 1), ('Директора', 2)]:
            self.assertIn(f'{kind} <span>{count}</span>', html)
        self.assertEqual(html.count('<table class="nr-table">'), 3)
        self.assertIn('Для проверки финансистом', html)
        self.assertIn('Годовая доплата за кварталы', html)

    def test_pagination_opens_selected_panel_and_keeps_full_totals(self):
        context = self.context()
        html = render_to_string('admin_motivation/reports_next/_team_ledger.html', context)
        self.assertIn('page_policies=3#premium-page_policies', html)
        self.assertIn('id="premium-page_policies"', html)
        self.assertEqual(context['presentation']['total'], Decimal(65))
        self.assertEqual([section['total'] for section in context['presentation']['team_sections']], [57, 2, 6])
        self.assertEqual(context['presentation']['team_sections'][0]['page'].paginator.count, 57)

    def test_filtered_registry_does_not_add_other_type_tabs(self):
        context = self.context(['Директора'])
        html = render_to_string('admin_motivation/reports_next/_team_ledger.html', context)
        self.assertEqual(html.count('role="tab"'), 1)
        self.assertIn('Директора <span>2</span>', html)
        self.assertNotIn('Политики', html)
        self.assertNotIn('Продажи', html)
