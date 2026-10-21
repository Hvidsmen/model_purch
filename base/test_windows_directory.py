import ctypes
from ctypes import wintypes
from unittest.mock import MagicMock, patch
from django.test import SimpleTestCase
from .windows_directory import lookup_account, WindowsDirectoryError


class WindowsSidLookupTests(SimpleTestCase):
    def api(self, domain='TT', kind=1):
        api = MagicMock()
        def write(pointer, value):
            ctypes.cast(pointer, ctypes.POINTER(wintypes.DWORD))[0] = value
        def lookup_name(system, qualified, sid, sid_size, resolved_domain, domain_size, use):
            self.assertIsNone(system)
            self.assertEqual(qualified, 'TT\\akrasnoperov')
            write(sid_size, 64)
            write(domain_size, 32)
            write(use, kind)
            if sid is None:
                return False
            resolved_domain.value = domain
            return True
        def lookup_sid(system, sid, name, name_size, canonical_domain, domain_size, use):
            write(name_size, 64)
            write(domain_size, 32)
            write(use, kind)
            if name is None:
                return False
            name.value = 'AKrasnoperov'
            canonical_domain.value = domain
            return True
        api.LookupAccountNameW.side_effect = lookup_name
        api.LookupAccountSidW.side_effect = lookup_sid
        return api

    def test_exact_trusted_account_resolves_canonical_sid_identity_without_controller(self):
        api = self.api()
        with patch('base.windows_directory.ctypes.WinDLL', return_value=api, create=True), patch('base.windows_directory.ctypes.get_last_error', return_value=122, create=True), patch('base.windows_directory._display_name', return_value='Красноперов Иван'):
            self.assertEqual(lookup_account('TT', 'akrasnoperov'), [{'username': 'tt\\akrasnoperov', 'full_name': 'Красноперов Иван'}])
        self.assertEqual(api.LookupAccountNameW.call_count, 2)
        self.assertEqual(api.LookupAccountSidW.call_count, 2)

    def test_group_computer_and_other_domain_are_not_imported_as_selected_user(self):
        for domain, kind in [('TT', 2), ('TT', 9), ('DAICHI', 1)]:
            with self.subTest(domain=domain, kind=kind), patch('base.windows_directory.ctypes.WinDLL', return_value=self.api(domain, kind), create=True), patch('base.windows_directory.ctypes.get_last_error', return_value=122, create=True):
                self.assertEqual(lookup_account('TT', 'akrasnoperov'), [])

    def test_absent_account_is_distinct_from_windows_rpc_failure(self):
        api = MagicMock()
        with patch('base.windows_directory.ctypes.WinDLL', return_value=api, create=True), patch('base.windows_directory.ctypes.get_last_error', return_value=1332, create=True):
            self.assertEqual(lookup_account('TT', 'akrasnoperov'), [])
        with patch('base.windows_directory.ctypes.WinDLL', return_value=api, create=True), patch('base.windows_directory.ctypes.get_last_error', return_value=1722, create=True):
            with self.assertRaisesRegex(WindowsDirectoryError, 'LookupAccountNameW: ошибка Windows 1722'):
                lookup_account('TT', 'akrasnoperov')

    def test_diagnostic_does_not_require_password_or_change_portal(self):
        from io import StringIO
        from django.core.management import call_command
        output = StringIO()
        with patch('base.management.commands.check_ad_account.search_accounts', return_value=[{'username': 'tt\\akrasnoperov', 'full_name': ''}]):
            call_command('check_ad_account', username='TT\\akrasnoperov', stdout=output)
        self.assertIn('tt\\akrasnoperov', output.getvalue())
        self.assertIn('Пароль не запрашивался', output.getvalue())
