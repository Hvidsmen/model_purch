from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from .access import ADMIN, LOGISTICIAN
from django.contrib.auth.models import Group

BACKEND = 'base.windows_auth.WindowsRemoteUserBackend'
MIDDLEWARE = [
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'base.preview.UserPreviewMiddleware',
    'base.middleware.PortalAccessMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
]


@override_settings(WINDOWS_AUTH_ENABLED=True, WINDOWS_AUTH_DOMAINS=('CONTOSO',),
                   AUTHENTICATION_BACKENDS=[BACKEND, 'django.contrib.auth.backends.ModelBackend'], MIDDLEWARE=MIDDLEWARE)
class WindowsAuthTests(TestCase):
    def test_windows_identity_created_without_password_or_privileges(self):
        response = self.client.get(reverse('portal_windows_login'), REMOTE_USER='CONTOSO\\Alice')
        self.assertEqual(response.status_code, 302)
        user = get_user_model().objects.get(username='contoso\\alice')
        self.assertFalse(user.has_usable_password())
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)
        self.assertFalse(user.groups.exists())
        self.assertEqual(self.client.get(reverse('scenario_list'), REMOTE_USER='CONTOSO\\Alice').status_code, 403)

    def test_assigned_role_is_preserved_and_username_case_is_stable(self):
        user = get_user_model().objects.create_user('contoso\\alice')
        user.groups.add(Group.objects.get(name=LOGISTICIAN))
        self.client.get(reverse('portal_windows_login'), REMOTE_USER='CONTOSO\\Alice')
        self.assertEqual(self.client.get(reverse('scenario_list')).status_code, 200)
        self.assertEqual(self.client.get('/admin/', REMOTE_USER='contoso\\ALICE').status_code, 403)
        self.assertEqual(get_user_model().objects.filter(username__icontains='alice').count(), 1)

    def test_http_identity_headers_are_not_trusted(self):
        user = get_user_model().objects.create_user('contoso\\admin', is_staff=True)
        user.groups.add(Group.objects.get(name=ADMIN))
        response = self.client.get(reverse('portal_windows_login'), HTTP_REMOTE_USER='CONTOSO\\admin', HTTP_X_REMOTE_USER='CONTOSO\\admin')
        self.assertEqual(response.status_code, 403)
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_unknown_domain_malformed_identity_and_disabled_account_rejected(self):
        for identity in ['OTHER\\alice', 'alice', 'CONTOSO\\', 'CONTOSO\\a\\b', 'CONTOSO\\a b']:
            with self.subTest(identity=identity):
                self.assertEqual(self.client.get(reverse('portal_windows_login'), REMOTE_USER=identity).status_code, 403)
        user = get_user_model().objects.create_user('contoso\\alice', is_active=False)
        self.assertEqual(self.client.get(reverse('portal_windows_login'), REMOTE_USER='CONTOSO\\alice').status_code, 403)
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_domain_identity_does_not_take_over_local_account(self):
        local = get_user_model().objects.create_superuser('alice', password='local-test')
        self.client.get(reverse('portal_windows_login'), REMOTE_USER='CONTOSO\\alice')
        self.assertNotEqual(str(local.pk), self.client.session['_auth_user_id'])

    def test_windows_session_persists_on_anonymous_iis_routes(self):
        self.client.get(reverse('portal_windows_login'), REMOTE_USER='CONTOSO\\alice')
        self.assertEqual(self.client.get('/').status_code, 200)
        self.assertIn('_auth_user_id', self.client.session)

    @override_settings(WINDOWS_AUTH_ENABLED=False)
    def test_disabled_integration_does_not_authenticate(self):
        self.assertEqual(self.client.get(reverse('portal_windows_login'), REMOTE_USER='CONTOSO\\alice').status_code, 404)
        self.assertFalse(get_user_model().objects.filter(username='contoso\\alice').exists())

    def test_windows_login_rejects_external_redirect(self):
        response = self.client.get(reverse('portal_windows_login'), {'next': 'https://example.org/'}, REMOTE_USER='CONTOSO\\alice')
        self.assertEqual(response.url, '/')
        self.assertEqual(self.client.post(reverse('portal_windows_login')).status_code, 405)

    def test_windows_identity_switches_account_without_reusing_local_privileges(self):
        user = get_user_model().objects.create_superuser('local-admin', password='test')
        self.client.force_login(user, backend='django.contrib.auth.backends.ModelBackend')
        self.client.get(reverse('portal_windows_login'), REMOTE_USER='CONTOSO\\alice')
        self.assertNotEqual(self.client.session['_auth_user_id'], str(user.pk))
        self.assertEqual(self.client.get('/admin/').status_code, 403)
