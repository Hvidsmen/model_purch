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
