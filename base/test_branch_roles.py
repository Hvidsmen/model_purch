from datetime import date
from unittest.mock import MagicMock, patch
from django.contrib.auth.models import User, Group
from django.core.exceptions import PermissionDenied
from django.test import TestCase
from django.urls import reverse
from admin_motivation.models import Chanel, Subdivision, TypeCoeff
from admin_motivation.services.reports import choices, load_report
from .access import DEPUTY_DIRECTOR, BRANCH_DIRECTOR, role_for
from .models import PortalReportGroup, MotivationReportAccess
from .report_access import politician_scope, scoped_values, identity_choices
from .user_views import UserForm


class BranchRoleTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser('branch-admin')
        channel = Chanel.objects.create(chanel_name='Branch role tests')
        self.branches = [Subdivision.objects.create(subdivision_key=name, subdivision_name=name, subdivision_global=name, chanel=channel) for name in ('Allowed-A', 'Allowed-B', 'Denied-C')]
        for name in ('Политики', 'Директора', 'Продажи'):
            TypeCoeff.objects.get_or_create(type_coeff_name=name)
        self.group = Group.objects.create(name='Selected branches')
        scope = PortalReportGroup.objects.create(group=self.group, employee_type=TypeCoeff.objects.get(type_coeff_name='Директора'))
        scope.subdivisions.set(self.branches[:2])
        self.users = {}
        for index, role in enumerate((DEPUTY_DIRECTOR, BRANCH_DIRECTOR)):
            user = User.objects.create_user(f'branch-role-{index}')
            user.groups.add(Group.objects.get(name=role), self.group)
            self.users[role] = user

    def test_admin_assigns_either_role_with_group_without_personal_author_or_department(self):
        for index, role in enumerate((DEPUTY_DIRECTOR, BRANCH_DIRECTOR)):
            form = UserForm({'username': f'TT\\branch{index}', 'portal_role': role, 'groups': [self.group.pk], 'is_active': 'on'}, actor=self.admin)
            self.assertTrue(form.is_valid(), form.errors)
            user = form.save()
            self.assertEqual(role_for(user), role)
            self.assertFalse(user.is_staff)
            self.assertFalse(MotivationReportAccess.objects.filter(user=user).exists())

    def test_role_requires_configured_branch_group(self):
        for role in (DEPUTY_DIRECTOR, BRANCH_DIRECTOR):
            form = UserForm({'username': 'TT\\branch', 'portal_role': role, 'report_subdivision': self.branches[0].pk, 'is_active': 'on'}, actor=self.admin)
            self.assertFalse(form.is_valid())
            self.assertIn('groups', form.errors)

    def test_scope_and_tampered_filters_keep_branch_boundaries_and_distinct_employee_access(self):
        for role, user in self.users.items():
            scope = politician_scope(user)
            self.assertTrue(scope['all_authors'])
            self.assertEqual(scope['subdivision'], ['Allowed-A', 'Allowed-B'])
            self.assertNotIn('author', scope)
            values = scoped_values('manager-detail', {'TypeCoeff_': 'Директора', 'Subdivision': ['Denied-C'], 'DealAuthor': ['Любой сотрудник']}, scope)
            self.assertEqual(values['Subdivision'], ['Allowed-A', 'Allowed-B'])
            self.assertEqual(values['DealAuthor'], ['Любой сотрудник'])
            self.assertEqual(values['TypeCoeff_'], 'Политики' if role == DEPUTY_DIRECTOR else 'Директора')
            self.assertNotIn('author', identity_choices('manager-detail', values, scope))

    def test_catalog_and_other_endpoints_are_read_only_and_scoped(self):
        for role, user in self.users.items():
            self.client.force_login(user)
            response = self.client.get(reverse('motivation_reports'))
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.content.count(b'report-catalog-card" href='), 3)
            for url in ('/accounts/users/', '/accounts/groups/', '/admin/', reverse('scenario_list'), reverse('global_coeff_admin_motivation')):
                self.assertEqual(self.client.get(url).status_code, 403)
            self.assertEqual(self.client.post(reverse('motivation_reports')).status_code, 403)
            if role == DEPUTY_DIRECTOR:
                self.assertEqual(self.client.get(reverse('motivation_report', args=['subdivisions'])).status_code, 403)

    def test_report_view_forces_scope_for_director_and_deputy(self):
        for role, user in self.users.items():
            self.client.force_login(user)
            with patch('admin_motivation.report_views.choices', return_value=[]), patch('admin_motivation.report_views.load_report', return_value={'currency': 'USD'}) as load:
                response = self.client.get(reverse('motivation_report', args=['manager-detail']), {'run': '1', 'type': 'Продажи', 'subdivision': 'Denied-C'})
            self.assertEqual(response.status_code, 200)
            values = load.call_args.args[1]
            self.assertEqual(values['Subdivision'], ['Allowed-A', 'Allowed-B'])
            self.assertEqual(values['TypeCoeff_'], 'Политики' if role == DEPUTY_DIRECTOR else 'Продажи')
            self.assertEqual(values['DealAuthor'], [])
            self.assertTrue(load.call_args.kwargs['access_scope']['all_authors'])

    def test_author_lookups_and_every_data_query_are_bounded_to_selected_branch_and_type(self):
        for role, user in self.users.items():
            scope = politician_scope(user)
            values = {'TypeCoeff_': 'Политики', 'Subdivision': 'Allowed-A', 'DealAuthor': None, 'qm': '1 Квартал'}
            with patch('admin_motivation.services.personal_reports.connect_database', return_value=(MagicMock(), MagicMock())), patch('admin_motivation.services.personal_reports.fetch', return_value=[{'value': 'Любой сотрудник'}]) as fetch:
                options = choices('manager', values, access_scope=scope)
            author = next(item for item in options if item['name'] == 'author')
            self.assertFalse(author['locked'])
            self.assertEqual(author['options'][0]['value'], 'Любой сотрудник')
            for call in fetch.call_args_list:
                self.assertIn('Allowed-A', call.args[2])
                self.assertIn('Политики', call.args[2])
                self.assertNotIn('Denied-C', call.args[2])
            kinds = ('manager', 'manager-detail', 'deals')
            for kind in kinds:
                params = {**values, 'Years': [], 'gp_sales': [], 'GPGoods': [], 'Brend': [], 'TypeCoeff': 'Директора', 'Division': 'Denied-C', 'Channel': [], 'ManagerName': [], 'GPSales': [], 'Partner': [], 'StartDate': date(2025, 1, 1), 'EndDate': date(2025, 12, 31), 'type_coeff': ['Директора']}
                with patch('admin_motivation.services.reports.connect_database', return_value=(MagicMock(), MagicMock())), patch('admin_motivation.services.reports.fetch', return_value=[]) as fetch:
                    load_report(kind, params, access_scope=scope)
                self.assertTrue(fetch.called)
                for call in fetch.call_args_list:
                    self.assertIn('Allowed-A', call.args[2])
                    self.assertNotIn('Denied-C', call.args[2])
                    if role == DEPUTY_DIRECTOR:
                        self.assertIn('Политики', call.args[2])
                        self.assertNotIn('Директора', call.args[2])

    def test_removing_groups_revokes_all_report_access_even_with_old_personal_profile(self):
        for role, user in self.users.items():
            MotivationReportAccess.objects.create(user=user, subdivision=self.branches[2], deal_author='Личный автор')
            user.groups.remove(self.group)
            self.assertIsNone(politician_scope(User.objects.get(pk=user.pk)))
            self.client.force_login(user)
            self.assertEqual(self.client.get(reverse('motivation_reports')).status_code, 403)
            with self.assertRaises(PermissionDenied):
                scoped_values('subdivisions', {}, {'subdivision': ['Allowed-A'], 'type': ['Политики'], 'all_authors': True, 'reports': ('manager',)})

    def test_excel_and_csv_exports_use_same_branch_and_employee_scope(self):
        from io import BytesIO
        for role, user in self.users.items():
            self.client.force_login(user)
            with patch('admin_motivation.report_views.load_report', return_value={'currency': 'USD'}) as load, patch('admin_motivation.services.report_excel.report_excel', return_value=BytesIO(b'file')) as export:
                response = self.client.get(reverse('motivation_report', args=['manager-detail']), {'export': 'xlsx', 'subdivision': 'Denied-C', 'type': 'Директора'})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(export.call_args.args[1]['Subdivision'], ['Allowed-A', 'Allowed-B'])
                self.assertEqual(load.call_args.args[1]['TypeCoeff_'], 'Политики' if role == DEPUTY_DIRECTOR else 'Директора')
                response.close()
            data = {'details': [], 'summary': [], 'totals': {}, 'offset': 0, 'page': 1, 'pages': 1}
            with patch('admin_motivation.report_views.choices', return_value=[]), patch('admin_motivation.report_views.load_report', return_value=data) as load:
                response = self.client.get(reverse('motivation_report', args=['deals']), {'run': '1', 'export': 'csv', 'subdivision': 'Denied-C', 'type': 'Директора'})
            self.assertEqual(response.status_code, 200)
            self.assertIn('text/csv', response['Content-Type'])
            self.assertEqual(load.call_args.args[1]['Division'], 'Allowed-A')
            self.assertEqual(load.call_args.args[1]['TypeCoeff'], 'Политики' if role == DEPUTY_DIRECTOR else 'Директора')

    def test_director_only_has_three_reports_and_cannot_open_other_report_urls(self):
        for user in self.users.values():
            self.client.force_login(user)
            self.assertEqual(self.client.get(reverse('motivation_plan_summary')).status_code, 403)
            self.assertEqual(self.client.get(reverse('motivation_report', args=['subdivisions'])).status_code, 403)
            self.assertEqual(self.client.get(reverse('motivation_report', args=['subdivisions']), {'export': 'xlsx'}).status_code, 403)

    def test_deputy_director_bonus_uses_own_identity_for_all_reports_and_queries(self):
        from admin_motivation.models import DirectorEmployee
        user = self.users[DEPUTY_DIRECTOR]
        user.first_name, user.last_name = 'Максим Игоревич', 'Волков'
        user.save()
        DirectorEmployee.objects.create(full_name='Волков Максим Игоревич')
        scope = politician_scope(user)
        self.assertEqual(scope['type'], ['Политики', 'Директора'])
        for kind in ('manager', 'manager-detail', 'deals'):
            with self.subTest(kind=kind):
                type_key, branch_key, author_key = ('TypeCoeff', 'Division', 'ManagerName') if kind == 'deals' else ('TypeCoeff_', 'Subdivision', 'DealAuthor')
                values = {type_key: 'Директора', branch_key: ['Denied-C'], author_key: ['Чужой директор'],
                          'Years': [], 'qm': '1 Квартал', 'gp_sales': [], 'GPGoods': [], 'Brend': [],
                          'StartDate': date(2025, 1, 1), 'EndDate': date(2025, 12, 31),
                          'Channel': [], 'GPSales': [], 'Partner': []}
                bounded = scoped_values(kind, values, scope)
                self.assertEqual(bounded[author_key], ['Волков Максим Игоревич'] if kind != 'manager' else 'Волков Максим Игоревич')
                identity = identity_choices(kind, bounded, scope)
                self.assertTrue(identity['author']['locked'])
                self.assertEqual(identity['author']['locked_value'], 'Волков Максим Игоревич')
                with patch('admin_motivation.services.personal_reports.connect_database', return_value=(MagicMock(), MagicMock())), patch('admin_motivation.services.personal_reports.fetch', return_value=[]) as fetch:
                    choices(kind, values, access_scope=scope)
                self.assertTrue(fetch.called)
                for call in fetch.call_args_list:
                    self.assertIn('Волков Максим Игоревич', call.args[2])
                    self.assertNotIn('Чужой директор', call.args[2])
                    self.assertNotIn('Denied-C', call.args[2])
                with patch('admin_motivation.services.reports.connect_database', return_value=(MagicMock(), MagicMock())), patch('admin_motivation.services.reports.fetch', return_value=[]) as fetch:
                    load_report(kind, values, access_scope=scope)
                self.assertTrue(fetch.called)
                for call in fetch.call_args_list:
                    self.assertIn('Волков Максим Игоревич', call.args[2])
                    self.assertIn('Директора', call.args[2])
                    self.assertNotIn('Чужой директор', call.args[2])
                    self.assertNotIn('Denied-C', call.args[2])
        politicians = scoped_values('manager-detail', {'TypeCoeff_': 'Политики', 'DealAuthor': ['Другой политик']}, scope)
        self.assertEqual(politicians['DealAuthor'], ['Другой политик'])
        self.assertNotIn('author', identity_choices('manager-detail', politicians, scope))

    def test_deputy_director_own_author_cannot_be_changed_in_page_or_export(self):
        from io import BytesIO
        user = self.users[DEPUTY_DIRECTOR]
        user.first_name, user.last_name = 'Максим Игоревич', 'Волков'
        user.save()
        self.client.force_login(user)
        url = reverse('motivation_report', args=['manager-detail'])
        query = {'type': 'Директора', 'author': 'Чужой директор', 'subdivision': 'Denied-C', 'run': '1'}
        with patch('admin_motivation.report_views.choices', return_value=[]), patch('admin_motivation.report_views.load_report', return_value={'currency': 'USD'}) as load:
            response = self.client.get(url, query)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(load.call_args.args[1]['DealAuthor'], ['Волков Максим Игоревич'])
        author = next(item for item in response.context['filters'] if item['name'] == 'author')
        self.assertTrue(author['locked'])
        self.assertEqual(author['locked_value'], 'Волков Максим Игоревич')
        with patch('admin_motivation.report_views.load_report', return_value={'currency': 'USD'}) as load, patch('admin_motivation.services.report_excel.report_excel', return_value=BytesIO(b'file')) as export:
            response = self.client.get(url, {**query, 'export': 'xlsx'})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(load.call_args.args[1]['DealAuthor'], ['Волков Максим Игоревич'])
            self.assertEqual(export.call_args.args[1]['DealAuthor'], ['Волков Максим Игоревич'])
            response.close()
