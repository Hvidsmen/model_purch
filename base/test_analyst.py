from unittest.mock import patch
from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse
from .access import ADMIN, ANALYST, ensure_roles, can_access, role_for
from .models import MotivationReportAccess
from .report_access import politician_scope


class AnalystRoleTests(TestCase):
    def setUp(self):
        ensure_roles()
        self.admin = User.objects.create_superuser('analyst-admin')
        self.user = User.objects.create_user('analyst-user')
        self.user.groups.add(Group.objects.get(name=ANALYST))
        self.client.force_login(self.user)

    def test_role_can_be_assigned_without_branch_or_employee_and_inherited_from_group(self):
        from .admin import PortalUserChangeForm
        from .user_views import UserForm
        from .group_views import ReportGroupForm
        self.assertIn((ANALYST, ANALYST), PortalUserChangeForm().fields['portal_role'].choices)
        form = UserForm({'username': 'DOMAIN\\analyst', 'portal_role': ANALYST, 'is_active': 'on'}, actor=self.admin)
        self.assertTrue(form.is_valid(), form.errors)
        saved = form.save()
        self.assertEqual(role_for(saved), ANALYST)
        self.assertFalse(saved.is_staff)
        self.assertFalse(MotivationReportAccess.objects.filter(user=saved).exists())
        group_form = ReportGroupForm({'name': 'Analytics team', 'portal_role': ANALYST, 'members': [self.user.pk]})
        self.assertTrue(group_form.is_valid(), group_form.errors)
        group = group_form.save()
        self.user.groups.remove(Group.objects.get(name=ANALYST))
        user = User.objects.get(pk=self.user.pk)
        self.assertTrue(user.groups.filter(pk=group.pk).exists())
        self.assertEqual(role_for(user), ANALYST)
        self.assertTrue(can_access(user, 'dashboards'))
        self.assertFalse(can_access(user, 'motivation'))
        self.assertFalse(can_access(user, 'purchases'))
        self.assertFalse(can_access(user, 'admin'))
        self.assertIsNone(politician_scope(user))

    def test_home_has_only_dashboards_and_other_projects_are_blocked(self):
        response = self.client.get(reverse('index_base'))
        self.assertContains(response, 'Дашборды')
        self.assertContains(response, reverse('dashboards_index'))
        self.assertEqual(response.content.count(b'class="portal-project-card"'), 1)
        self.assertNotContains(response, reverse('global_coeff_admin_motivation'))
        self.assertNotContains(response, reverse('scenario_list'))
        for name in ('global_coeff_admin_motivation', 'motivation_reports', 'scenario_list', 'portal_users', 'portal_user_groups', 'loader_motive'):
            self.assertEqual(self.client.get(reverse(name)).status_code, 403, name)
            self.assertEqual(self.client.post(reverse(name)).status_code, 403, name)
        self.assertEqual(self.client.get('/admin/').status_code, 403)

    def test_dashboard_read_access_and_no_writes(self):
        for name in ('dashboards_index', 'dealer_dashboard'):
            response = self.client.get(reverse(name))
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, 'Аналитик')
        from dashboards.tests import fixture
        with patch('dashboards.views.load_sales', return_value={'rows': fixture(), 'loaded_at': '2026-10-24T10:00:00Z', 'end_exclusive': '2026-10-01'}):
            self.assertEqual(self.client.get(reverse('dealer_dashboard_data')).status_code, 200)
            self.assertEqual(self.client.get(reverse('dealer_dashboard_export')).status_code, 200)
        for name in ('dealer_dashboard_data', 'dealer_dashboard_export', 'dashboards_index'):
            self.assertEqual(self.client.post(reverse(name)).status_code, 405)

    def test_admin_preview_uses_analyst_permissions_and_returns_to_admin(self):
        self.client.force_login(self.admin)
        self.client.post(reverse('portal_user_preview'), {'user': self.user.pk})
        response = self.client.get(reverse('dashboards_index'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Аналитик')
        self.assertEqual(self.client.get(reverse('global_coeff_admin_motivation')).status_code, 403)
        self.client.post(reverse('portal_stop_preview'))
        self.assertEqual(self.client.get(reverse('global_coeff_admin_motivation')).status_code, 200)
