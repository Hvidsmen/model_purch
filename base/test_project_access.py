from unittest.mock import patch
from django.contrib.auth.models import User, Group
from django.test import TestCase
from django.urls import reverse
from admin_motivation.models import Chanel, Subdivision, TypeCoeff
from .access import (ADMIN, ANALYST, FINANCIER, POLITICIAN, LOGISTICIAN, BRANCH_DIRECTOR,
                     role_for, can_access, ensure_roles)
from .models import UserProjectAccess, GroupProjectAccess, PortalReportGroup
from .user_views import UserForm
from .group_views import ReportGroupForm
from .report_access import politician_scope


class ProjectAccessTests(TestCase):
    def setUp(self):
        ensure_roles()
        self.admin = User.objects.create_superuser('projects-admin')
        self.user = User.objects.create_user('separate-project-user')
        self.channel = Chanel.objects.create(chanel_name='Project scope')
        self.branch = Subdivision.objects.create(subdivision_key='Allowed', subdivision_name='Allowed', subdivision_global='Allowed', chanel=self.channel)
        self.other = Subdivision.objects.create(subdivision_key='Denied', subdivision_name='Denied', subdivision_global='Denied', chanel=self.channel)
        TypeCoeff.objects.get_or_create(type_coeff_name='Политики')

    def grant(self, project, role):
        UserProjectAccess.objects.update_or_create(user=self.user, project=project, defaults={'role': role})

    def test_three_roles_coexist_without_conflict(self):
        for project, role in [('motivation', FINANCIER), ('purchases', LOGISTICIAN), ('dashboards', ANALYST)]:
            self.grant(project, role)
            self.assertEqual(role_for(self.user, project), role)
            self.assertTrue(can_access(self.user, project))
        self.assertFalse(can_access(self.user, 'admin'))
        self.client.force_login(self.user)
        page = self.client.get(reverse('index_base'))
        self.assertEqual(page.content.count(b'class="portal-project-card"'), 3)
        self.assertEqual(self.client.get(reverse('motivation_discount_segments')).status_code, 200)
        self.assertEqual(self.client.get(reverse('scenario_list')).status_code, 200)
        self.assertEqual(self.client.get(reverse('dashboards_index')).status_code, 200)

    def test_project_admin_does_not_become_portal_admin_or_gain_other_projects(self):
        self.grant('motivation', ADMIN)
        self.assertTrue(can_access(self.user, 'motivation_admin'))
        self.assertFalse(can_access(self.user, 'admin'))
        self.client.force_login(self.user)
        page = self.client.get(reverse('global_coeff_admin_motivation'))
        self.assertEqual(page.status_code, 200)
        self.assertTrue(page.context['portal_can_administer_motivation'])
        self.assertFalse(page.context['portal_is_admin'])
        for route in ('portal_users', 'portal_user_groups', 'scenario_list', 'dashboards_index'):
            self.assertEqual(self.client.get(reverse(route)).status_code, 403)
        self.assertEqual(self.client.get('/admin/').status_code, 403)
        # Permission gate accepts project administrators before business validation.
        from admin_motivation.operation_views import admin_required
        from django.test import RequestFactory
        request = RequestFactory().post('/')
        request.user = self.user
        admin_required(request)

    def test_personal_deny_beats_group_and_removing_override_restores_group(self):
        group = Group.objects.create(name='Analyst group')
        GroupProjectAccess.objects.create(group=group, project='dashboards', role=ANALYST)
        self.user.groups.add(group)
        self.assertTrue(can_access(self.user, 'dashboards'))
        self.grant('dashboards', '')
        self.assertFalse(can_access(self.user, 'dashboards'))
        UserProjectAccess.objects.filter(user=self.user).delete()
        self.assertTrue(can_access(self.user, 'dashboards'))

    def test_project_conflicts_fail_only_that_project(self):
        for index, role in enumerate((FINANCIER, BRANCH_DIRECTOR)):
            group = Group.objects.create(name=f'Mixed projects {index}')
            GroupProjectAccess.objects.create(group=group, project='motivation', role=role)
            GroupProjectAccess.objects.create(group=group, project='dashboards', role=ANALYST)
            self.user.groups.add(group)
        self.assertIsNone(role_for(self.user, 'motivation'))
        self.assertEqual(role_for(self.user, 'dashboards'), ANALYST)
        self.grant('motivation', FINANCIER)
        self.assertEqual(role_for(self.user, 'motivation'), FINANCIER)

    def test_report_only_motivation_role_does_not_block_independent_dashboards(self):
        self.grant('motivation', BRANCH_DIRECTOR)
        self.grant('dashboards', ANALYST)
        group = Group.objects.create(name='Director boundaries')
        scope = PortalReportGroup.objects.create(group=group, portal_role=BRANCH_DIRECTOR)
        scope.subdivisions.add(self.branch)
        self.user.groups.add(group)
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(reverse('dashboards_index')).status_code, 200)
        self.assertEqual(self.client.get(reverse('motivation_reports')).status_code, 200)
        self.assertEqual(self.client.get(reverse('global_coeff_admin_motivation')).status_code, 403)

    def test_no_motivation_branch_leak_from_other_project_groups(self):
        self.grant('motivation', BRANCH_DIRECTOR)
        for branch, role in [(self.branch, BRANCH_DIRECTOR), (self.other, '')]:
            group = Group.objects.create(name='Scope ' + branch.subdivision_key)
            scope = PortalReportGroup.objects.create(group=group, portal_role=BRANCH_DIRECTOR)
            scope.subdivisions.add(branch)
            GroupProjectAccess.objects.create(group=group, project='motivation', role=role)
            GroupProjectAccess.objects.create(group=group, project='dashboards', role=ANALYST)
            self.user.groups.add(group)
        self.assertEqual(politician_scope(self.user)['subdivision'], ['Allowed'])

    def test_user_and_group_forms_save_independent_assignments_and_validate_motivation_scope(self):
        form = UserForm({'username': 'DOMAIN\\separate', 'portal_role': '', 'motivation_role': FINANCIER,
                         'purchases_role': LOGISTICIAN, 'dashboards_role': ANALYST, 'is_active': 'on'}, actor=self.admin)
        self.assertTrue(form.is_valid(), form.errors)
        saved = form.save()
        self.assertFalse(saved.is_staff)
        self.assertEqual(role_for(saved, 'motivation'), FINANCIER)
        self.assertEqual(role_for(saved, 'purchases'), LOGISTICIAN)
        self.assertEqual(role_for(saved, 'dashboards'), ANALYST)
        group_form = ReportGroupForm({'name': 'Separate group', 'motivation_role': '', 'purchases_role': LOGISTICIAN, 'dashboards_role': ANALYST, 'members': [self.user.pk]})
        self.assertTrue(group_form.is_valid(), group_form.errors)
        group = group_form.save()
        self.assertFalse(can_access(User.objects.get(pk=self.user.pk), 'motivation'))
        self.assertEqual(role_for(User.objects.get(pk=self.user.pk), 'dashboards'), ANALYST)
        invalid = UserForm({'username': 'DOMAIN\\no-scope', 'motivation_role': POLITICIAN,
                           'purchases_role': '', 'dashboards_role': ANALYST, 'is_active': 'on'}, actor=self.admin)
        self.assertFalse(invalid.is_valid())
        self.assertIn('report_subdivision', invalid.errors)

    def test_group_edit_cannot_lock_out_its_only_portal_administrator(self):
        group = Group.objects.create(name='Only global administrator group')
        PortalReportGroup.objects.create(group=group, portal_role=ADMIN)
        self.user.groups.add(group)
        form = ReportGroupForm({'name': group.name, 'portal_role': '', 'motivation_role': '',
                                'purchases_role': '', 'dashboards_role': ADMIN, 'members': [self.user.pk]},
                               group=group, actor=self.user)
        self.assertFalse(form.is_valid())
        self.assertIn('общего администратора', str(form.errors))

    def test_legacy_rights_and_global_admin_are_preserved(self):
        self.user.groups.add(Group.objects.get(name=LOGISTICIAN))
        self.assertEqual(role_for(self.user, 'purchases'), LOGISTICIAN)
        self.assertIsNone(role_for(self.user, 'motivation'))
        for project in ('motivation', 'purchases', 'dashboards'):
            self.assertEqual(role_for(self.admin, project), ADMIN)
        self.admin.is_active = False
        self.assertFalse(can_access(self.admin, 'dashboards'))
