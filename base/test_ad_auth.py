import ssl
from importlib.util import find_spec
from unittest import skipUnless
from unittest.mock import MagicMock, patch
from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import PermissionDenied
from django.test import TestCase, override_settings
from django.urls import reverse
from .ad_auth import ActiveDirectoryBackend, directory_identity
from .access import ADMIN

SETTINGS = dict(AD_AUTH_ENABLED=True, AD_LDAPS_URL='ldaps://dc.example.test:636', AD_NETBIOS_DOMAIN='CONTOSO',
                AD_UPN_SUFFIX='example.test', AD_SEARCH_BASE='DC=example,DC=test', AD_CA_CERT_FILE='', AD_REQUIRE_HTTPS=True,
                AUTHENTICATION_BACKENDS=['base.ad_auth.ActiveDirectoryBackend', 'django.contrib.auth.backends.ModelBackend'])


@override_settings(**SETTINGS)
class ActiveDirectoryTests(TestCase):
    def identity(self):
        return dict(username='contoso\\alice', first_name='Alice', last_name='Employee', email='alice@example.test')

    def test_success_creates_no_password_and_no_privileges(self):
        with patch('base.ad_auth.directory_identity', return_value=self.identity()):
            response = self.client.post(reverse('portal_login'), {'username': 'CONTOSO\\alice', 'password': 'not-saved'}, secure=True)
        self.assertEqual(response.status_code, 302)
        user = get_user_model().objects.get(username='contoso\\alice')
        self.assertFalse(user.has_usable_password())
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)
        self.assertFalse(user.groups.exists())
        self.assertNotIn('not-saved', str(dict(self.client.session)))
        self.assertEqual(user.first_name, 'Alice')

    def test_local_login_remains_available_without_contacting_directory(self):
        user = get_user_model().objects.create_user('local-admin', password='local-test')
        with patch('base.ad_auth.directory_identity') as directory:
            response = self.client.post(reverse('portal_login'), {'username': user.username, 'password': 'local-test'})
        self.assertEqual(response.status_code, 302)
        directory.assert_not_called()

    def test_http_refuses_domain_credentials_before_contacting_directory(self):
        with patch('base.ad_auth.directory_identity') as directory:
            response = self.client.post(reverse('portal_login'), {'username': 'CONTOSO\\alice', 'password': 'secret'})
        self.assertEqual(response.status_code, 200)
        directory.assert_not_called()
        self.assertFalse(get_user_model().objects.filter(username='contoso\\alice').exists())
        self.assertContains(self.client.get(reverse('portal_login')), 'не вводите доменный пароль')

    @override_settings(AD_REQUIRE_HTTPS=False, AD_AUTH_METHOD='windows')
    def test_explicit_http_windows_login_preserves_role_without_storing_password(self):
        user = get_user_model().objects.create_user('contoso\\alice', is_staff=True)
        user.groups.add(Group.objects.get(name=ADMIN))
        with patch('base.windows_password.windows_password_identity', return_value={'username': user.username}) as windows:
            response = self.client.post(reverse('portal_login'), {'username': 'CONTOSO\\alice', 'password': 'not-saved'})
        self.assertEqual(response.status_code, 302)
        windows.assert_called_once_with('CONTOSO\\alice', 'not-saved')
        self.assertEqual(self.client.session['_auth_user_id'], str(user.pk))
        self.assertNotIn('not-saved', str(dict(self.client.session)))
        user.refresh_from_db()
        self.assertTrue(user.groups.filter(name=ADMIN).exists())
        self.client.logout()
        response = self.client.get(reverse('portal_login'))
        self.assertContains(response, 'CONTOSO\\логин')
        self.assertNotContains(response, 'не вводите доменный пароль')

    def test_upn_reuses_directory_sam_name_and_preserves_existing_role(self):
        user = get_user_model().objects.create_user('contoso\\alice', is_staff=True)
        user.groups.add(Group.objects.get(name=ADMIN))
        with patch('base.ad_auth.directory_identity', return_value=self.identity()):
            self.client.post(reverse('portal_login'), {'username': 'different-upn@example.test', 'password': 'secret'}, secure=True)
        self.assertEqual(self.client.session['_auth_user_id'], str(user.pk))
        user.refresh_from_db()
        self.assertTrue(user.is_staff)
        self.assertTrue(user.groups.filter(name=ADMIN).exists())

    def test_ldap_failure_never_falls_back_to_local_password_for_domain_name(self):
        get_user_model().objects.create_user('contoso\\alice', password='local-password')
        with patch('base.ad_auth.directory_identity', side_effect=RuntimeError('Server unavailable')):
            response = self.client.post(reverse('portal_login'), {'username': 'contoso\\alice', 'password': 'local-password'}, secure=True)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_disabled_portal_account_is_not_reactivated(self):
        user = get_user_model().objects.create_user('contoso\\alice', is_active=False)
        with patch('base.ad_auth.directory_identity', return_value=self.identity()):
            self.client.post(reverse('portal_login'), {'username': 'CONTOSO\\alice', 'password': 'secret'}, secure=True)
        self.assertNotIn('_auth_user_id', self.client.session)
        user.refresh_from_db()
        self.assertFalse(user.is_active)

    @skipUnless(find_spec('ldap3'), 'Optional AD dependency: requirements-ad.txt')
    def test_ldaps_validates_certificate_escapes_filter_and_disconnects(self):
        entry = MagicMock()
        entry.entry_attributes_as_dict = {'sAMAccountName': ['alice'], 'givenName': ['Alice'], 'userAccountControl': [512]}
        connection = MagicMock()
        connection.entries = [entry]
        with patch('ldap3.Server') as server, patch('ldap3.Tls') as tls, patch('ldap3.Connection', return_value=connection) as client:
            identity = directory_identity('alice*)(x=1)@example.test', 'test-secret')
        self.assertEqual(identity['username'], 'contoso\\alice')
        self.assertEqual(tls.call_args.kwargs['validate'], ssl.CERT_REQUIRED)
        self.assertTrue(server.call_args.kwargs['use_ssl'])
        self.assertFalse(client.call_args.kwargs['auto_referrals'])
        self.assertIn(r'\2a\29\28', connection.search.call_args.args[1])
        connection.unbind.assert_called_once()

    @skipUnless(find_spec('ldap3'), 'Optional AD dependency: requirements-ad.txt')
    def test_bind_failure_disconnects_and_creates_nothing(self):
        connection = MagicMock()
        connection.bind.return_value = False
        with patch('ldap3.Connection', return_value=connection), self.assertRaises(PermissionDenied):
            directory_identity('CONTOSO\\alice', 'wrong')
        connection.unbind.assert_called_once()
        connection.search.assert_not_called()

    @skipUnless(find_spec('ldap3'), 'Optional AD dependency: requirements-ad.txt')
    def test_ambiguous_or_disabled_directory_entry_is_rejected(self):
        for entries in ([], [MagicMock(), MagicMock()], [MagicMock(entry_attributes_as_dict={'sAMAccountName': ['alice'], 'userAccountControl': [514]})]):
            connection = MagicMock()
            connection.entries = entries
            with patch('ldap3.Connection', return_value=connection), self.assertRaises(PermissionDenied):
                directory_identity('CONTOSO\\alice', 'test')
            connection.unbind.assert_called_once()

    @skipUnless(find_spec('ldap3'), 'Optional AD dependency: requirements-ad.txt')
    def test_disallowed_domain_and_plain_ldap_never_bind(self):
        with patch('ldap3.Connection') as client:
            with self.assertRaises(PermissionDenied):
                directory_identity('OTHER\\alice', 'secret')
            with self.settings(AD_LDAPS_URL='ldap://dc.example.test'):
                with self.assertRaises(ValueError):
                    directory_identity('CONTOSO\\alice', 'secret')
        client.assert_not_called()

    def test_diagnostic_checks_tls_without_a_password(self):
        from io import StringIO
        from django.core.management import call_command
        output = StringIO()
        with patch('base.management.commands.check_ad_connection.socket.create_connection') as tcp, patch('base.management.commands.check_ad_connection.ssl.create_default_context') as context:
            context.return_value.wrap_socket.return_value.__enter__.return_value.version.return_value = 'TLSv1.3'
            call_command('check_ad_connection', stdout=output)
        self.assertIn('Сертификат и имя сервера проверены', output.getvalue())
        self.assertIn('Пароль не отправлялся', output.getvalue())
        tcp.assert_called_once_with(('dc.example.test', 636), timeout=5)
        self.assertEqual(context.return_value.wrap_socket.call_args.kwargs['server_hostname'], 'dc.example.test')
