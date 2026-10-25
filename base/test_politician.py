from datetime import date
from io import BytesIO
from unittest.mock import Mock, patch
import re
from django.contrib.auth.models import Group, User
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.urls import reverse, URLPattern
from .access import ADMIN, POLITICIAN, ensure_roles
from .models import MotivationReportAccess
from admin_motivation.models import Chanel, Subdivision
from admin_motivation.services.reports import load_report, choices


class PoliticianAccessTests(TestCase):
    def setUp(self):
        ensure_roles()
        self.user = User.objects.create_user('daichi\\personal')
        self.user.groups.add(Group.objects.get(name=POLITICIAN))
        channel = Chanel.objects.create(chanel_name='Test channel')
        self.branch = Subdivision.objects.create(subdivision_key='Own', subdivision_name='Own', subdivision_global='Own', chanel=channel)
        self.scope = {'subdivision': 'Own', 'author': 'Иванов Иван Иванович', 'type': 'Политики', 'pilot_periods': True}
        MotivationReportAccess.objects.create(user=self.user, subdivision=self.branch, deal_author=self.scope['author'])
        self.client.force_login(self.user)

    def test_pilot_is_trusted_role_setting_and_does_not_apply_to_sales(self):
        from .access import SALES, MOTIVATION_VIEWER
        from .report_access import politician_scope
        self.assertTrue(politician_scope(self.user)['pilot_periods'])
        self.user.groups.clear()
        self.user.groups.add(Group.objects.get(name=SALES))
        self.assertNotIn('pilot_periods', politician_scope(User.objects.get(pk=self.user.pk)))
        for role in (ADMIN, MOTIVATION_VIEWER):
            self.user.groups.clear()
            self.user.groups.add(Group.objects.get(name=role))
            self.assertIsNone(politician_scope(User.objects.get(pk=self.user.pk)))

    def test_only_three_reports_and_no_coefficient_or_plan_navigation(self):
        response = self.client.get(reverse('motivation_reports'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content.count(b'report-catalog-card" href='), 3)
        for text in ['Мотивация. Дашборд', 'Мотивация. Детально', 'Факт закрытых сделок']:
            self.assertContains(response, text)
        for text in ['Глобальные коэффициенты', 'Сводный отчёт по плану продаж', 'Справочник директоров', 'Планы продаж', 'Выгрузка в DWH']:
            self.assertNotContains(response, text)

    def test_all_other_motivation_endpoints_and_purchase_admin_are_denied(self):
        from admin_motivation.urls import urlpatterns
        with patch('admin_motivation.report_views.load_report') as read:
            for pattern in urlpatterns:
                if not isinstance(pattern, URLPattern) or pattern.name in ('motivation_reports', 'motivation_reports_next'):
                    continue
                route = re.sub(r'<(?:[^:>]+:)?[^>]+>', 'subdivisions' if pattern.name == 'motivation_report' else '1', str(pattern.pattern))
                with self.subTest(route=route):
                    self.assertEqual(self.client.get('/admin_motivation' + route).status_code, 403)
                    self.assertEqual(self.client.post('/admin_motivation' + route).status_code, 403)
            for url in ['/admin/', reverse('scenario_list'), reverse('portal_user_preview'), reverse('portal_report_authors')]:
                self.assertEqual(self.client.get(url).status_code, 403)
        read.assert_not_called()

    def test_filter_tampering_and_repeated_parameters_are_replaced_before_data_access(self):
        for kind, author_key, branch_key, type_key in [('manager', 'DealAuthor', 'Subdivision', 'TypeCoeff_'), ('manager-detail', 'DealAuthor', 'Subdivision', 'TypeCoeff_'), ('deals', 'ManagerName', 'Division', 'TypeCoeff')]:
            with self.subTest(kind=kind), patch('admin_motivation.report_views.choices', return_value=[]) as options, patch('admin_motivation.report_views.load_report', side_effect=ValidationError('Нет данных')) as read:
                response = self.client.get(reverse('motivation_report', args=[kind]), {'run': '1', 'type': 'Директора', 'subdivision': ['Other', 'Second'], 'author': ['Other Employee', 'Second'], 'quarter': '1 Квартал', 'start': '2025-01-01', 'end': '2025-12-31'})
                self.assertEqual(response.status_code, 200)
                values = read.call_args.args[1]
                expected_list = kind != 'manager'
                self.assertEqual(values[author_key], [self.scope['author']] if expected_list else self.scope['author'])
                self.assertEqual(values[branch_key], ['Own'] if kind == 'manager-detail' else 'Own')
                self.assertEqual(values[type_key], 'Политики')
                self.assertEqual(read.call_args.kwargs['access_scope'], self.scope)
                for item in response.context['filters']:
                    if item['name'] in ('type', 'subdivision', 'author'):
                        self.assertTrue(item['locked'])
                        self.assertEqual(len(item['options']), 1)

    def test_missing_scope_and_disabled_user_cannot_load_reports(self):
        self.user.motivation_report_access.delete()
        self.assertEqual(self.client.get(reverse('motivation_reports')).status_code, 403)
        self.user.is_active = False
        self.user.save()
        self.assertEqual(self.client.get(reverse('motivation_reports')).status_code, 302)

    def test_excel_cannot_use_another_author(self):
        data = {'currency': 'USD'}
        with patch('admin_motivation.report_views.load_report', return_value=data) as read, patch('admin_motivation.services.report_excel.report_excel', return_value=BytesIO(b'file')) as export:
            response = self.client.get(reverse('motivation_report', args=['manager-detail']), {'export': 'xlsx', 'author': 'Other', 'subdivision': 'Other'})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(read.call_args.args[1]['DealAuthor'], [self.scope['author']])
            self.assertEqual(export.call_args.args[1]['Subdivision'], ['Own'])
            response.close()

    def test_data_queries_are_scoped_even_if_service_values_are_tampered(self):
        connection, cursor = Mock(), Mock()
        values = {'TypeCoeff_': 'Директора', 'Subdivision': ['Other'], 'DealAuthor': ['Other'], 'gp_sales': [], 'qm': '1 Квартал', 'Currency': 'USD', 'Years': [], 'GPGoods': [], 'Brend': []}
        with patch('admin_motivation.services.reports.connect_database', return_value=(connection, cursor)), patch('admin_motivation.services.reports.fetch', return_value=[]) as read:
            load_report('manager-detail', values, access_scope=self.scope)
            # Personal rows stay author-scoped. Full-branch performance contains
            # only sales/plan aggregates, without other employees or their payouts.
            self.assertIn(self.scope['author'], read.call_args_list[0].args[2])
            aggregate = read.call_args_list[1]
            self.assertNotIn('[АвторСделки]', aggregate.args[1])
            self.assertNotIn('СуммаМотивацииUSD', aggregate.args[1])
            self.assertIn('Own', aggregate.args[2])
            for call in read.call_args_list:
                self.assertNotIn('Other', call.args[2])
        with patch('admin_motivation.services.reports.connect_database', return_value=(connection, cursor)), patch('admin_motivation.services.reports.fetch', return_value=[]) as read:
            load_report('manager', values, access_scope=self.scope)
            first_sql = read.call_args_list[0].args[1]
            self.assertIn('AS personal_source WHERE [АвторСделки] IN', first_sql)
            for call in read.call_args_list:
                self.assertIn(self.scope['author'], call.args[2])
                self.assertNotIn('Other', call.args[2])
        with self.assertRaises(PermissionDenied):
            load_report('subdivisions', values, access_scope=self.scope)

    def test_lookup_lists_never_request_other_authors_or_subdivisions(self):
        connection, cursor = Mock(), Mock()
        values = {'TypeCoeff_': 'Директора', 'Subdivision': 'Other', 'DealAuthor': 'Other', 'gp_sales': []}
        with patch('admin_motivation.services.personal_reports.connect_database', return_value=(connection, cursor)), patch('admin_motivation.services.personal_reports.fetch', return_value=[{'value': 'OwnGroup'}]) as read:
            result = choices('manager', values, access_scope=self.scope)
        for call in read.call_args_list:
            self.assertIn(self.scope['author'], call.args[2])
            self.assertIn('Own', call.args[2])
            self.assertNotIn('Other', call.args[2])
        self.assertEqual(next(item for item in result if item['name'] == 'author')['options'], [{'value': self.scope['author'], 'selected': True}])

    def test_admin_must_assign_subdivision_and_author_for_politician(self):
        from .admin import PortalUserCreationForm
        form = PortalUserCreationForm({'username': 'DAICHI\\new', 'portal_role': POLITICIAN})
        self.assertFalse(form.is_valid())
        self.assertIn('report_subdivision', form.errors)
        self.assertIn('report_author', form.errors)
        form = PortalUserCreationForm({'username': 'DAICHI\\new', 'portal_role': POLITICIAN, 'report_subdivision': self.branch.pk, 'report_author': self.scope['author']})
        self.assertTrue(form.is_valid(), form.errors)

    def test_deal_queries_keep_author_branch_and_type_for_summary_and_rows(self):
        connection, cursor = Mock(), Mock()
        values = {'TypeCoeff': 'Директора', 'Division': 'Other', 'ManagerName': ['Other'],
                  'StartDate': date(2025,1,1), 'EndDate': date(2025,12,31), 'Channel': [], 'GPSales': [], 'GPGoods': [], 'Brend': [], 'Partner': []}
        with patch('admin_motivation.services.reports.connect_database', return_value=(connection, cursor)), patch('admin_motivation.services.reports.fetch', return_value=[]) as read:
            load_report('deals', values, access_scope=self.scope)
        self.assertEqual(read.call_count, 2)
        for call in read.call_args_list:
            self.assertIn(self.scope['author'], call.args[2])
            self.assertIn('Own', call.args[2])
            self.assertIn('Политики', call.args[2])
            self.assertNotIn('Other', call.args[2])
