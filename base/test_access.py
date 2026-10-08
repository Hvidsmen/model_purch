from django.contrib.auth.models import Group, Permission, User
from django.test import TestCase, Client
from django.urls import reverse, URLPattern
from .access import ADMIN, DIRECTOR, LOGISTICIAN, ROLES, ensure_roles
from .admin import PortalUserChangeForm, PortalUserAdmin
from django.contrib import admin
import re


class PortalAccessTests(TestCase):
    def setUp(self):
        ensure_roles()
        self.users = {}
        for i, role in enumerate(ROLES):
            user = User.objects.create_user(f'role-{i}', password='test-password', is_staff=role == ADMIN)
            user.groups.add(Group.objects.get(name=role))
            self.users[role] = user

    def test_anonymous_requires_login_and_api_returns_401(self):
        response = self.client.get(reverse('scenario_list'))
        self.assertRedirects(response, reverse('portal_login') + '?next=' + reverse('scenario_list'))
        response = self.client.post('/model_purch/api/algorithm/start/', {}, content_type='application/json')
        self.assertEqual(response.status_code, 401)
        self.assertIn('login_url', response.json())
        self.assertEqual(self.client.get(reverse('motivation_coefficient_comparison')).status_code, 401)

    def test_roles_see_only_their_projects(self):
        for role in ROLES:
            self.client.force_login(self.users[role])
            response = self.client.get('/')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(b'portal-project-card' in response.content, True)
            self.assertEqual('Сценарии, товары' in response.content.decode(), role in (ADMIN, LOGISTICIAN))
            self.assertEqual('Коэффициенты, версии' in response.content.decode(), role in (ADMIN, DIRECTOR))
            self.assertEqual('Другие разделы' in response.content.decode(), role == ADMIN)
            for name, allowed in [('scenario_list', role in (ADMIN, LOGISTICIAN)), ('global_coeff_admin_motivation', role in (ADMIN, DIRECTOR))]:
                self.assertEqual(self.client.get(reverse(name)).status_code, 200 if allowed else 403)
            self.assertEqual(self.client.get('/admin/').status_code, 200 if role == ADMIN else 403)

    def test_every_project_endpoint_rejects_wrong_role_before_view_runs(self):
        from model_purch.urls import urlpatterns as purchases
        from admin_motivation.urls import urlpatterns as motivation
        for role, prefix, patterns in [(DIRECTOR, '/model_purch/', purchases), (LOGISTICIAN, '/admin_motivation', motivation)]:
            self.client.force_login(self.users[role])
            for pattern in patterns:
                if not isinstance(pattern, URLPattern):
                    continue
                route = re.sub(r'<(?:[^:>]+:)?[^>]+>', '1', str(pattern.pattern))
                for method in ('get', 'post'):
                    with self.subTest(role=role, route=route, method=method):
                        response = getattr(self.client, method)(prefix + route)
                        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.client.get(reverse('index')).status_code, 403)
        self.assertEqual(self.client.get(reverse('re_ref_store_group')).status_code, 403)

    def test_no_role_and_conflicting_roles_fail_closed(self):
        user = User.objects.create_user('unassigned', is_staff=True)
        self.client.force_login(user)
        self.assertContains(self.client.get('/'), 'Роль не назначена')
        self.assertEqual(self.client.get(reverse('scenario_list')).status_code, 403)
        user.groups.add(Group.objects.get(name=DIRECTOR), Group.objects.get(name=LOGISTICIAN))
        self.assertEqual(self.client.get(reverse('global_coeff_admin_motivation')).status_code, 403)
        user.is_superuser = True
        user.save()
        self.assertEqual(self.client.get(reverse('scenario_list')).status_code, 200)

    def test_login_safe_redirect_logout_and_inactive_account(self):
        user = self.users[LOGISTICIAN]
        response = self.client.post(reverse('portal_login'), {'username': user.username, 'password': 'test-password', 'next': 'https://example.org/'})
        self.assertRedirects(response, '/')
        self.assertEqual(self.client.get(reverse('portal_logout')).status_code, 405)
        self.assertRedirects(self.client.post(reverse('portal_logout')), reverse('portal_login'))
        user.is_active = False
        user.save()
        self.assertContains(self.client.post(reverse('portal_login'), {'username': user.username, 'password': 'test-password'}), 'errorlist')

    def test_logout_requires_csrf(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.users[ADMIN])
        self.assertEqual(client.post(reverse('portal_logout')).status_code, 403)

    def test_admin_permissions_and_role_assignment(self):
        self.assertEqual(Group.objects.get(name=ADMIN).permissions.count(), Permission.objects.count())
        user = self.users[DIRECTOR]
        custom = Group.objects.create(name='Other group')
        data = {'username': user.username, 'is_active': True, 'portal_role': LOGISTICIAN, 'groups': [custom.pk], 'date_joined': user.date_joined}
        form = PortalUserChangeForm(data=data, instance=user)
        self.assertTrue(form.is_valid(), form.errors)
        obj = form.save(commit=False)
        model_admin = PortalUserAdmin(User, admin.site)
        model_admin.save_model(None, obj, form, True)
        model_admin.save_related(None, form, [], True)
        user.refresh_from_db()
        self.assertFalse(user.is_staff)
        self.assertEqual(set(user.groups.values_list('name', flat=True)), {LOGISTICIAN, 'Other group'})
        data['portal_role'] = ADMIN
        form = PortalUserChangeForm(data=data, instance=user)
        self.assertTrue(form.is_valid(), form.errors)
        model_admin.save_model(None, form.save(commit=False), form, True)
        model_admin.save_related(None, form, [], True)
        user.refresh_from_db()
        self.assertTrue(user.is_staff)
        self.client.force_login(user)
        self.assertEqual(self.client.get(reverse('admin:auth_user_changelist')).status_code, 200)
