from unittest.mock import MagicMock, patch
from django.core.exceptions import PermissionDenied
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from .windows_password import windows_password_identity


@override_settings(AD_AUTH_ENABLED=True, AD_AUTH_METHOD='windows', AD_NETBIOS_DOMAIN='DAICHI', AD_UPN_SUFFIX='daichi.local',
    AUTHENTICATION_BACKENDS=['base.ad_auth.ActiveDirectoryBackend', 'django.contrib.auth.backends.ModelBackend'])
class WindowsPasswordTests(TestCase):
    def test_native_validation_closes_token_and_uses_network_logon(self):
        api, kernel = MagicMock(), MagicMock()
        with patch('base.windows_password._load_api', return_value=(api, kernel)), patch('base.windows_password._token_account', return_value=('DAICHI', 'RealSam')):
            result = windows_password_identity('alias@daichi.local', 'test-password')
        self.assertEqual(result, {'username': 'daichi\\realsam'})
        self.assertEqual(api.LogonUserW.call_args.args[:5], ('alias@daichi.local', None, 'test-password', 3, 0))
        kernel.CloseHandle.assert_called_once()

    def test_rejects_other_domain_and_closes_successful_token_on_resolution_failure(self):
        api, kernel = MagicMock(), MagicMock()
        with patch('base.windows_password._load_api', return_value=(api, kernel)), patch('base.windows_password._token_account', return_value=('OTHER', 'Admin')):
            with self.assertRaises(PermissionDenied):
                windows_password_identity('DAICHI\\alice', 'test')
        kernel.CloseHandle.assert_called_once()

    def test_invalid_credentials_and_foreign_domain_never_create_identity(self):
        api, kernel = MagicMock(), MagicMock()
        api.LogonUserW.return_value = False
        with patch('base.windows_password._load_api', return_value=(api, kernel)):
            for name in ['OTHER\\alice', 'alice@other.local', 'DAICHI\\alice', 'DAICHI\\a\x00b']:
                with self.subTest(name=name), self.assertRaises(PermissionDenied):
                    windows_password_identity(name, 'test')
        kernel.CloseHandle.assert_not_called()

    def test_native_login_preserves_profile_and_local_login(self):
        user = get_user_model().objects.create_user('daichi\\alice', first_name='Existing', email='existing@example.test')
        with patch('base.windows_password.windows_password_identity', return_value={'username': 'daichi\\alice'}), patch('base.ad_auth.directory_identity') as ldap:
            response = self.client.post(reverse('portal_login'), {'username': 'DAICHI\\alice', 'password': 'test'}, secure=True)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.session['_auth_user_id'], str(user.pk))
        user.refresh_from_db()
        self.assertEqual(user.first_name, 'Existing')
        self.assertEqual(user.email, 'existing@example.test')
        ldap.assert_not_called()

    def test_native_user_created_without_roles_or_password(self):
        with patch('base.windows_password.windows_password_identity', return_value={'username': 'daichi\\new'}):
            self.client.post(reverse('portal_login'), {'username': 'DAICHI\\new', 'password': 'secret'}, secure=True)
        user = get_user_model().objects.get(username='daichi\\new')
        self.assertFalse(user.has_usable_password())
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)
        self.assertFalse(user.groups.exists())

    def test_domain_failure_does_not_accept_local_password(self):
        get_user_model().objects.create_user('daichi\\alice', password='local-test')
        with patch('base.windows_password.windows_password_identity', side_effect=PermissionDenied):
            self.client.post(reverse('portal_login'), {'username': 'DAICHI\\alice', 'password': 'local-test'}, secure=True)
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_diagnostic_prompts_privately_without_creating_portal_user(self):
        from io import StringIO
        from django.core.management import call_command
        out = StringIO()
        with patch('base.management.commands.check_windows_ad.getpass', return_value='not-printed'), patch('base.management.commands.check_windows_ad.windows_password_identity', return_value={'username': 'daichi\\alice'}):
            call_command('check_windows_ad', username='DAICHI\\alice', stdout=out)
        self.assertIn('LDAP/LDAPS не использовались', out.getvalue())
        self.assertNotIn('not-printed', out.getvalue())
        self.assertFalse(get_user_model().objects.filter(username='daichi\\alice').exists())

    def test_domain_format_validation_happens_before_native_api(self):
        with patch('base.windows_password._load_api') as api:
            for username in ['OTHER\\alice', 'alice@other.local', 'DAICHI\\', 'DAICHI\\a b']:
                with self.subTest(username=username), self.assertRaises(PermissionDenied):
                    windows_password_identity(username, 'secret')
        api.assert_not_called()

    def test_child_domain_login_and_upn_use_windows_canonical_identity(self):
        api, kernel = MagicMock(), MagicMock()
        domains = {'daichi': 'daichi.local', 'tt': 'tt.daichi.local'}
        with patch('base.windows_domains.domain_map', return_value=domains), patch('base.windows_password._load_api', return_value=(api, kernel)), patch('base.windows_password._token_account', return_value=('TT', 'Krasnoperov')):
            self.assertEqual(windows_password_identity('TT\\Krasnoperov', 'test'), {'username': 'tt\\krasnoperov'})
            self.assertEqual(api.LogonUserW.call_args.args[:2], ('Krasnoperov', 'TT'))
            self.assertEqual(windows_password_identity('Krasnoperov@tt.daichi.local', 'test'), {'username': 'tt\\krasnoperov'})
            self.assertEqual(api.LogonUserW.call_args.args[:2], ('Krasnoperov@tt.daichi.local', None))
        self.assertEqual(kernel.CloseHandle.call_count, 2)

    def test_another_forests_domain_and_mismatched_token_rejected(self):
        api, kernel = MagicMock(), MagicMock()
        with patch('base.windows_domains.domain_map', return_value={'daichi': 'daichi.local', 'tt': 'tt.daichi.local'}), patch('base.windows_password._load_api', return_value=(api, kernel)), patch('base.windows_password._token_account', return_value=('DAICHI', 'Krasnoperov')):
            with self.assertRaises(PermissionDenied):
                windows_password_identity('TT\\Krasnoperov', 'test')
            with self.assertRaises(PermissionDenied):
                windows_password_identity('EXTERNAL\\Krasnoperov', 'test')
        self.assertEqual(api.LogonUserW.call_count, 1)
        kernel.CloseHandle.assert_called_once()

    def test_trusted_domain_netbios_and_dns_are_independent(self):
        api, kernel = MagicMock(), MagicMock()
        with patch('base.windows_domains.domain_map', return_value={'daichi': 'daichi.local', 'tt': 'thermotrade.local'}), patch('base.windows_password._load_api', return_value=(api, kernel)), patch('base.windows_password._token_account', return_value=('TT', 'Krasnoperov')):
            for login in ('TT\\Krasnoperov', 'Krasnoperov@thermotrade.local'):
                self.assertEqual(windows_password_identity(login, 'test-password'), {'username': 'tt\\krasnoperov'})
        self.assertEqual(kernel.CloseHandle.call_count, 2)
