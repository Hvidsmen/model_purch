from unittest.mock import patch
from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse
from .access import ADMIN, POLITICIAN, ensure_roles
from .models import MotivationReportAccess, PortalReportGroup
from .report_access import politician_scope, scoped_values, identity_choices
from admin_motivation.models import Subdivision, TypeCoeff, Chanel


class ReportGroupTests(TestCase):
    def setUp(self):
        ensure_roles()
        self.admin = User.objects.create_user('groups-admin', is_staff=True)
        self.admin.groups.add(Group.objects.get(name=ADMIN))
        self.user = User.objects.create_user('groups-politician')
        self.user.groups.add(Group.objects.get(name=POLITICIAN))
        channel = Chanel.objects.create(chanel_name='Group test')
        self.branches = [Subdivision.objects.create(subdivision_key=name, subdivision_name=name, subdivision_global=name, chanel=channel) for name in ('Moscow', 'Volga', 'Crimea')]
        self.policies = TypeCoeff.objects.create(type_coeff_name='Политики')
        self.directors = TypeCoeff.objects.create(type_coeff_name='Директора')
        MotivationReportAccess.objects.create(user=self.user, subdivision=self.branches[0], deal_author='Личный Автор Сделки')
        self.client.force_login(self.admin)

    def group(self, name, worker, branches):
        group = Group.objects.create(name=name)
        scope = PortalReportGroup.objects.create(group=group, employee_type=worker)
        scope.subdivisions.set(branches)
        group.user_set.add(self.user)
        return group

    def test_admin_creates_group_with_departments_worker_and_members(self):
        response = self.client.post(reverse('portal_user_groups'), {'name': 'Team', 'employee_type': self.policies.pk,
            'subdivisions': [branch.pk for branch in self.branches[:2]], 'members': [self.user.pk]})
        self.assertEqual(response.status_code, 302)
        group = Group.objects.get(name='Team')
        self.assertEqual(group.portal_scope.employee_type, self.policies)
        self.assertEqual(group.portal_scope.subdivisions.count(), 2)
        self.assertEqual(list(group.user_set.all()), [self.user])
        self.assertTrue(self.user.groups.filter(name=POLITICIAN).exists())

    def test_missing_departments_does_not_mean_all(self):
        response = self.client.post(reverse('portal_user_groups'), {'name': 'Empty', 'employee_type': self.policies.pk})
        self.assertEqual(response.status_code, 200)
        self.assertIn('subdivisions', response.context['form'].errors)
        self.assertFalse(Group.objects.filter(name='Empty').exists())

    def test_scope_unions_group_departments_but_never_crosses_worker_pairs(self):
        self.group('Policies', self.policies, self.branches[:2])
        self.group('Directors', self.directors, self.branches[2:])
        scope = politician_scope(self.user)
        self.assertEqual(scope['author'], 'Личный Автор Сделки')
        values = scoped_values('manager-detail', {'TypeCoeff_': 'Директора', 'Subdivision': ['Volga'], 'DealAuthor': ['Other']}, scope)
        self.assertEqual(values['Subdivision'], ['Crimea'])
        self.assertEqual(values['DealAuthor'], ['Личный Автор Сделки'])
        values = scoped_values('manager-detail', {'TypeCoeff_': 'Политики', 'Subdivision': []}, scope)
        self.assertEqual(values['Subdivision'], ['Moscow', 'Volga'])
        choices = identity_choices('manager-detail', values, scope)
        self.assertFalse(choices['subdivision']['locked'])
        self.assertTrue(choices['author']['locked'])
        self.assertEqual({row['value'] for row in choices['type']['options']}, {'Политики', 'Директора'})

    def test_group_change_applies_on_next_request_and_keeps_personal_author(self):
        group = self.group('Team', self.policies, self.branches[:2])
        self.client.force_login(self.user)
        with patch('admin_motivation.report_views.choices', return_value=[]):
            response = self.client.get(reverse('motivation_report', args=['manager-detail']))
        self.assertEqual(set(response.context['query'].getlist('subdivision')), {'Moscow', 'Volga'})
        group.portal_scope.subdivisions.set(self.branches[1:2])
        with patch('admin_motivation.report_views.choices', return_value=[]):
            response = self.client.get(reverse('motivation_report', args=['manager-detail']), {'subdivision': 'Moscow', 'author': 'Other'})
        self.assertEqual(response.context['query'].getlist('subdivision'), ['Volga'])
        self.assertEqual(response.context['query']['author'], 'Личный Автор Сделки')

    def test_group_admin_endpoint_rejects_other_roles_and_system_group_edit(self):
        self.assertEqual(self.client.get(reverse('portal_user_groups'), {'edit': Group.objects.get(name=POLITICIAN).pk}).status_code, 404)
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(reverse('portal_user_groups')).status_code, 403)
        self.assertEqual(self.client.post(reverse('portal_user_groups'), {'name': 'Forbidden'}).status_code, 403)
        self.assertFalse(Group.objects.filter(name='Forbidden').exists())

    def test_editing_group_preserves_permissions_and_replaces_members(self):
        group = self.group('Team', self.policies, self.branches[:1])
        response = self.client.post(reverse('portal_user_groups'), {'group': group.pk, 'name': 'Changed', 'employee_type': self.directors.pk,
            'subdivisions': [self.branches[2].pk], 'members': []})
        self.assertEqual(response.status_code, 302)
        group.refresh_from_db()
        self.assertEqual(group.name, 'Changed')
        self.assertEqual(group.portal_scope.employee_type_id, self.directors.pk)
        self.assertEqual(group.user_set.count(), 0)

    def test_sql_queries_do_not_cross_worker_department_pairs(self):
        from unittest.mock import Mock
        from admin_motivation.services.reports import load_report
        self.group('Policies', self.policies, self.branches[:2])
        self.group('Directors', self.directors, self.branches[2:])
        scope = politician_scope(self.user)
        values = {'TypeCoeff_': 'Директора', 'Subdivision': ['Volga'], 'DealAuthor': ['Other'],
                  'gp_sales': [], 'GPGoods': [], 'Brend': [], 'Years': [], 'Currency': 'USD'}
        with patch('admin_motivation.services.reports.connect_database', return_value=(Mock(), Mock())), patch('admin_motivation.services.reports.fetch', return_value=[]) as read:
            load_report('manager-detail', values, access_scope=scope)
        for call in read.call_args_list:
            self.assertIn('Crimea', call.args[2])
            self.assertIn('Директора', call.args[2])
            self.assertIn('Личный Автор Сделки', call.args[2])
            self.assertNotIn('Volga', call.args[2])
            self.assertNotIn('Other', call.args[2])

    def test_reserved_role_name_cannot_be_created_as_a_group(self):
        response = self.client.post(reverse('portal_user_groups'), {'name': 'политик', 'employee_type': self.policies.pk,
            'subdivisions': [self.branches[0].pk]})
        self.assertEqual(response.status_code, 200)
        self.assertIn('name', response.context['form'].errors)
