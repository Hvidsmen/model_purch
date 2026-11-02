from contextlib import nullcontext
from copy import deepcopy
from datetime import date
from uuid import uuid4
from unittest.mock import patch
from django.contrib.auth.models import User, Group
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from base.access import POLITICIAN
from base.models import MotivationReportAccess
from .models import MotivationExecution, Chanel, Subdivision
from .services.deal_presentation import COLUMNS


def deal_payload():
    row = {key: 'Значение ' + key for key, _, _ in COLUMNS}
    row.update(АвторСделки='Иванов', ДатаЗакрытияСделки=date(2026, 10, 30), ДатаОтгрузки=date(2026, 10, 28),
               ПроцентСкидки=22.04, КоэфициентМотивации='.006', СуммаОтгрузокUSD=1000,
               СуммаКвМотивацииUSD=80, СуммаМотивацииUSDГод=20)
    return {'summary': [{'author': 'Иванов', 'shipments': 1000, 'quarterly': 80, 'annual': 20, 'possible': 100}],
            'totals': {'count': 101, 'shipments': 1000, 'quarterly': 80, 'annual': 20, 'possible': 100},
            'details': [row], 'page': 2, 'pages': 3, 'offset': 50}


class ClosedDealsNextTests(TestCase):
    def setUp(self):
        self.actor = User.objects.create_superuser('deals-admin')
        self.client.force_login(self.actor)
        self.run = MotivationExecution.objects.create(run_id=uuid4(), job_key=uuid4(), state='completed',
            started_at=timezone.now(), completed_at=timezone.now(), parameters={'fact_year': 2026})
        self.url = reverse('motivation_report_next', args=['deals'])
        self.query = {'run': '1', 'type': 'Политики', 'subdivision': 'A', 'start': '2026-01-01', 'end': '2026-11-03', 'page': '2'}

    def test_new_catalog_has_deals_and_uses_all_legacy_fields_and_filters(self):
        self.assertContains(self.client.get(reverse('motivation_reports_next')), 'Факт закрытых сделок')
        filters = {**self.query, 'author': ['Иванов'], 'channel': ['Retail'], 'planning': ['RAC'], 'goods': ['Split'], 'brand': ['Brand'], 'partner': ['Partner']}
        with patch('admin_motivation.reports_next_deals.choices', return_value=[]), patch('admin_motivation.services.reports_next.current_run', return_value=nullcontext()) as guard, patch('admin_motivation.reports_next_deals.load_report', return_value=deal_payload()) as load:
            response = self.client.get(self.url, filters)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '30.10.2026')
        self.assertContains(response, '28.10.2026')
        self.assertContains(response, '22,04%')
        self.assertContains(response, '0,60%')
        self.assertEqual(len(response.context['data']['columns']), 18)
        guard.assert_called_once_with(self.run)
        self.assertEqual(load.call_args.args[0], 'deals')
        values = load.call_args.args[1]
        self.assertEqual(values['Channel'], ['Retail'])
        self.assertEqual(values['GPSales'], ['RAC'])
        self.assertEqual(values['Partner'], ['Partner'])
        self.assertEqual(values['StartDate'], date(2026, 1, 1))
        self.assertEqual(load.call_args.kwargs['page'], 2)
        self.assertIn('page=3', response.context['data']['next_url'])
        self.assertIn('partner=Partner', response.context['data']['next_url'])
        self.assertNotContains(response, 'name="execution"')

    def test_csv_is_identical_to_legacy_including_hidden_fields_and_formula_protection(self):
        payload = deal_payload()
        payload['details'][0]['Партнер'] = '=HYPERLINK("bad")'
        with patch('admin_motivation.report_views.choices', return_value=[]), patch('admin_motivation.report_views.load_report', return_value=deepcopy(payload)):
            old = self.client.get(reverse('motivation_report', args=['deals']), {**self.query, 'export': 'csv'})
        with patch('admin_motivation.reports_next_deals.choices', return_value=[]), patch('admin_motivation.services.reports_next.current_run', return_value=nullcontext()), patch('admin_motivation.reports_next_deals.load_report', return_value=deepcopy(payload)):
            new = self.client.get(self.url, {**self.query, 'export': 'csv'})
        self.assertEqual(new.content, old.content)
        self.assertIn("'=HYPERLINK", new.content.decode('utf-8-sig'))
        self.assertIn('Дата отгрузки', new.content.decode('utf-8-sig'))

    def test_scoped_employee_cannot_override_author_branch_or_type(self):
        channel = Chanel.objects.create(chanel_name='Channel')
        branch = Subdivision.objects.create(subdivision_key='A', subdivision_name='A', subdivision_global='A', chanel=channel)
        user = User.objects.create_user('deals-politician')
        user.groups.add(Group.objects.get(name=POLITICIAN))
        MotivationReportAccess.objects.create(user=user, subdivision=branch, deal_author='Trusted employee')
        self.client.force_login(user)
        with patch('admin_motivation.reports_next_deals.choices', return_value=[]), patch('admin_motivation.services.reports_next.current_run', return_value=nullcontext()), patch('admin_motivation.reports_next_deals.load_report', return_value=deal_payload()) as load:
            response = self.client.get(self.url, {**self.query, 'author': 'Other', 'subdivision': 'Other', 'type': 'Директора'})
        self.assertEqual(response.status_code, 200)
        values = load.call_args.args[1]
        self.assertEqual(values['Division'], 'A')
        self.assertEqual(values['TypeCoeff'], 'Политики')
        self.assertEqual(values['ManagerName'], ['Trusted employee'])
        self.assertEqual(load.call_args.kwargs['access_scope']['author'], 'Trusted employee')
        user.motivation_report_access.delete()
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_invalid_dates_and_empty_results_are_explicit(self):
        response = self.client.get(self.url, {**self.query, 'start': '2027-01-01'})
        self.assertContains(response, 'Дата окончания раньше даты начала')
        with patch('admin_motivation.reports_next_deals.choices', return_value=[]), patch('admin_motivation.services.reports_next.current_run', return_value=nullcontext()), patch('admin_motivation.reports_next_deals.load_report', return_value={'summary': [], 'totals': {'count': 0, 'shipments': 0, 'quarterly': 0, 'annual': 0, 'possible': 0}, 'details': [], 'page': 1, 'pages': 1, 'offset': 0}):
            response = self.client.get(self.url, self.query)
        self.assertContains(response, 'Нет сделок по выбранным условиям.')
