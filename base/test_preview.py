from django.contrib.auth.models import User, Group
from django.test import TestCase, Client
from django.urls import reverse
from .access import ADMIN, DIRECTOR, LOGISTICIAN, ensure_roles
from .preview import SESSION_KEY


class UserPreviewTests(TestCase):
    def setUp(self):
        ensure_roles()
        self.admin = User.objects.create_user('preview-admin', password='test', is_staff=True)
        self.admin.groups.add(Group.objects.get(name=ADMIN))
        self.director = User.objects.create_user('preview-director')
        self.director.groups.add(Group.objects.get(name=DIRECTOR))
        self.logistician = User.objects.create_user('preview-logistician')
        self.logistician.groups.add(Group.objects.get(name=LOGISTICIAN))
        self.client.force_login(self.admin)

    def start(self, user):
        return self.client.post(reverse('portal_user_preview'), {'user': user.pk})

    def test_preview_permissions_and_safe_return_keep_original_session(self):
        self.assertEqual(self.start(self.director).status_code, 302)
        response = self.client.get('/')
        self.assertContains(response, 'Просмотр как preview-director')
        self.assertContains(response, 'Вернуться к администратору')
        self.assertNotContains(response, 'Сценарии, товары')
        self.assertEqual(self.client.get(reverse('scenario_list')).status_code, 403)
        self.assertEqual(self.client.get('/admin/').status_code, 403)
        self.assertEqual(self.client.session['_auth_user_id'], str(self.admin.pk))
        self.assertEqual(self.client.post(reverse('portal_stop_preview')).status_code, 302)
        self.assertNotIn(SESSION_KEY, self.client.session)
        self.assertEqual(self.client.get('/admin/').status_code, 200)
        self.assertEqual(list(self.director.groups.values_list('name', flat=True)), [DIRECTOR])

    def test_preview_is_readonly_even_if_target_has_permission(self):
        self.start(self.logistician)
        response = self.client.post(reverse('scenario_list'), {})
        self.assertContains(response, 'Только просмотр', status_code=403)
        response = self.client.post('/model_purch/api/test', {}, HTTP_ACCEPT='application/json')
        self.assertEqual(response.status_code, 403)
        self.assertIn('error', response.json())

    def test_only_admin_can_start_or_stop_and_inactive_target_is_rejected(self):
        self.director.is_active = False
        self.director.save()
        self.assertEqual(self.start(self.director).status_code, 200)
        self.assertNotIn(SESSION_KEY, self.client.session)
        self.client.force_login(self.logistician)
        self.assertEqual(self.start(self.admin).status_code, 403)
        self.assertEqual(self.client.post(reverse('portal_stop_preview')).status_code, 403)

    def test_preview_stops_if_admin_loses_role(self):
        self.start(self.logistician)
        self.admin.groups.clear()
        self.client.get('/')
        self.assertNotIn(SESSION_KEY, self.client.session)

    def test_preview_start_and_stop_require_csrf(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.admin)
        for route in ['portal_user_preview', 'portal_stop_preview']:
            self.assertEqual(client.post(reverse(route), {'user': self.logistician.pk}).status_code, 403)

    def test_local_login_form_remains_available(self):
        self.client.logout()
        with self.settings(WINDOWS_AUTH_ENABLED=True):
            response = self.client.get(reverse('portal_login'))
            self.assertContains(response, 'Войти через Windows')
            self.assertContains(response, 'name="password"')
            response = self.client.post(reverse('portal_login'), {'username': self.admin.username, 'password': 'test'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.session['_auth_user_id'], str(self.admin.pk))

    def test_admin_adds_domain_user_without_password_and_edits_role(self):
        response = self.client.post(reverse('admin:auth_user_add'), {'username': 'DAICHI\\NewUser', 'portal_role': DIRECTOR, '_save': 'Save'})
        self.assertEqual(response.status_code, 302)
        user = User.objects.get(username='daichi\\newuser')
        self.assertFalse(user.has_usable_password())
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)
        self.assertEqual(list(user.groups.values_list('name', flat=True)), [DIRECTOR])
        response = self.client.post(reverse('admin:auth_user_change', args=[user.pk]), {
            'username': user.username, 'first_name': 'Employee', 'last_name': '', 'email': '', 'is_active': 'on',
            'portal_role': LOGISTICIAN, 'date_joined_0': user.date_joined.strftime('%Y-%m-%d'),
            'date_joined_1': user.date_joined.strftime('%H:%M:%S'), '_save': 'Save'})
        self.assertEqual(response.status_code, 302)
        user.refresh_from_db()
        self.assertEqual(list(user.groups.values_list('name', flat=True)), [LOGISTICIAN])
        self.assertEqual(user.first_name, 'Employee')
        self.assertFalse(user.has_usable_password())

    def test_domain_user_case_duplicate_rejected_and_local_password_still_required(self):
        from .admin import PortalUserCreationForm
        User.objects.create_user('daichi\\exists')
        duplicate = PortalUserCreationForm({'username': 'DAICHI\\EXISTS', 'portal_role': DIRECTOR})
        self.assertFalse(duplicate.is_valid())
        self.assertIn('username', duplicate.errors)
        local = PortalUserCreationForm({'username': 'new-local', 'portal_role': LOGISTICIAN})
        self.assertFalse(local.is_valid())
        self.assertIn('password1', local.errors)
        malformed = PortalUserCreationForm({'username': 'DAICHI\\a\\b'})
        self.assertFalse(malformed.is_valid())

    def test_nonadmin_cannot_create_user_or_assign_role(self):
        self.client.force_login(self.logistician)
        response = self.client.post(reverse('admin:auth_user_add'), {'username': 'DAICHI\\forbidden', 'portal_role': ADMIN})
        self.assertEqual(response.status_code, 403)
        self.assertFalse(User.objects.filter(username__iexact='daichi\\forbidden').exists())

    def test_custom_groups_manage_members_without_changing_their_roles(self):
        response = self.client.post(reverse('admin:auth_group_add'), {'name': 'Test team', 'members': [self.director.pk, self.logistician.pk], '_save': 'Save'})
        self.assertEqual(response.status_code, 302)
        group = Group.objects.get(name='Test team')
        self.assertEqual(set(group.user_set.values_list('pk', flat=True)), {self.director.pk, self.logistician.pk})
        response = self.client.post(reverse('admin:auth_group_change', args=[group.pk]), {'name': group.name, 'members': [self.director.pk], '_save': 'Save'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(list(group.user_set.values_list('pk', flat=True)), [self.director.pk])
        self.assertTrue(self.logistician.groups.filter(name=LOGISTICIAN).exists())
        self.assertTrue(self.director.groups.filter(name=DIRECTOR).exists())
