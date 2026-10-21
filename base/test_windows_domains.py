import ctypes
from ctypes import wintypes
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from django.core.cache import cache
from django.test import SimpleTestCase, override_settings
from .windows_domains import domain_map, _read_forest_domains


@override_settings(AD_NETBIOS_DOMAIN='DAICHI', AD_UPN_SUFFIX='daichi.local', AD_FOREST_DISCOVERY=True)
class ForestDomainTests(SimpleTestCase):
    def tearDown(self):
        cache.clear()

    def test_only_windows_reported_forest_domains_are_included_and_buffer_freed(self):
        class Trust(ctypes.Structure):
            _fields_ = [('netbios', wintypes.LPWSTR), ('dns', wintypes.LPWSTR), ('flags', wintypes.ULONG), ('parent', wintypes.ULONG), ('trust_type', wintypes.ULONG), ('attributes', wintypes.ULONG), ('sid', ctypes.c_void_p), ('guid', ctypes.c_byte * 16)]
        rows = (Trust * 3)()
        for row, name, dns, flags in zip(rows, ['DAICHI', 'TT', 'EXTERNAL'], ['daichi.local', 'tt.daichi.local', 'external.test'], [1, 1, 2]):
            row.netbios, row.dns, row.flags = name, dns, flags
        api = MagicMock()
        def discover(server, flags, buffer, count):
            self.assertEqual(flags, 1)
            ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p))[0] = ctypes.cast(rows, ctypes.c_void_p)
            ctypes.cast(count, ctypes.POINTER(wintypes.ULONG))[0] = 3
            return 0
        api.DsEnumerateDomainTrustsW.side_effect = discover
        with patch('base.windows_domains.ctypes.WinDLL', return_value=api, create=True):
            self.assertEqual(_read_forest_domains(include_trusted=False), [{'netbios': 'DAICHI', 'dns': 'daichi.local'}, {'netbios': 'TT', 'dns': 'tt.daichi.local'}])
        api.NetApiBufferFree.assert_called_once()

    def test_discovery_and_cached_identity_map(self):
        with patch('base.windows_domains.os', SimpleNamespace(name='nt', environ={})):
            with patch('base.directory_search._load_catalog', return_value=[{'netbios': 'TT', 'dns': 'tt.daichi.local'}]) as load:
                self.assertEqual(domain_map(), {'daichi': 'daichi.local', 'tt': 'tt.daichi.local'})
                load.assert_called_once()
            cache.set('portal-ad-domains-v2:daichi::trusted', [{'netbios': 'TT', 'dns': 'tt.daichi.local'}], 300)
            with patch('base.directory_search._load_catalog') as load:
                self.assertEqual(domain_map()['tt'], 'tt.daichi.local')
                load.assert_not_called()

    @override_settings(AD_FOREST_DISCOVERY=False)
    def test_disabled_discovery_keeps_existing_single_domain(self):
        self.assertEqual(domain_map(), {'daichi': 'daichi.local'})

    def test_forest_discovery_subprocess_has_short_timeout(self):
        from .directory_search import _load_catalog
        with patch('base.directory_search.subprocess.run', return_value=SimpleNamespace(returncode=0, stdout=b'[]')) as run:
            _load_catalog('__forest__', '', 'trusted', 'forest-unit-test')
        self.assertEqual(run.call_args.kwargs['timeout'], 15)
        self.assertEqual(run.call_args.args[0][-3:], ['base.windows_domains', '', 'trusted'])

    def test_trusted_domains_outside_forest_are_included_but_unrelated_entries_are_not(self):
        class Trust(ctypes.Structure):
            _fields_ = [('netbios', wintypes.LPWSTR), ('dns', wintypes.LPWSTR), ('flags', wintypes.ULONG), ('parent', wintypes.ULONG), ('trust_type', wintypes.ULONG), ('attributes', wintypes.ULONG), ('sid', ctypes.c_void_p), ('guid', ctypes.c_byte * 16)]
        rows = (Trust * 5)()
        for row, name, dns, flags in zip(rows, ['DAICHI', 'TT', 'AMADA', 'LEGACY', 'UNKNOWN'], ['daichi.local', 'thermotrade.local', 'amada.local', '', 'unknown.test'], [1, 2, 32, 2, 0]):
            row.netbios, row.dns, row.flags = name, dns, flags
        api = MagicMock()
        def discover(server, flags, buffer, count):
            self.assertEqual(flags, 35)
            ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p))[0] = ctypes.cast(rows, ctypes.c_void_p)
            ctypes.cast(count, ctypes.POINTER(wintypes.ULONG))[0] = 5
            return 0
        api.DsEnumerateDomainTrustsW.side_effect = discover
        with patch('base.windows_domains.ctypes.WinDLL', return_value=api, create=True):
            self.assertEqual([row['netbios'] for row in _read_forest_domains()], ['DAICHI', 'TT', 'AMADA', 'LEGACY'])
        api.NetApiBufferFree.assert_called_once()

    @override_settings(AD_TRUSTED_DOMAIN_DISCOVERY=False)
    def test_forest_only_mode_passed_to_worker(self):
        with patch('base.windows_domains.os', SimpleNamespace(name='nt', environ={})):
            with patch('base.directory_search._load_catalog', return_value=[]) as load:
                domain_map()
                self.assertEqual(load.call_args.args[2], 'forest')

    def test_legacy_trust_without_dns_name_is_available_by_netbios(self):
        with patch('base.windows_domains.os', SimpleNamespace(name='nt', environ={})):
            with patch('base.directory_search._load_catalog', return_value=[{'netbios': 'LEGACY', 'dns': ''}]):
                self.assertEqual(domain_map()['legacy'], '')

    def test_diagnostic_shows_real_netbios_and_dns_without_changing_accounts(self):
        from io import StringIO
        from django.core.management import call_command
        output = StringIO()
        with patch('base.management.commands.check_ad_domains.domain_map', return_value={'tt': 'thermotrade.local'}):
            call_command('check_ad_domains', stdout=output)
        self.assertIn('TT — thermotrade.local', output.getvalue())
