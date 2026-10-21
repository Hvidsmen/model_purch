import json
import subprocess
from types import SimpleNamespace
from unittest.mock import patch
from django.core.cache import cache
from django.test import SimpleTestCase, override_settings
from .directory_search import search_accounts, _catalog_lock


@override_settings(AD_NETBIOS_DOMAIN='DAICHI')
class DirectorySearchTests(SimpleTestCase):
    def setUp(self):
        cache.clear()
        self.windows = patch('base.directory_search.os', SimpleNamespace(name='nt', environ={'PORTAL_AD_DIRECTORY_SERVER': 'dc.example.test'}))
        self.windows.start()
        self.addCleanup(self.windows.stop)
        self.addCleanup(cache.clear)

    def result(self):
        return SimpleNamespace(returncode=0, stdout=json.dumps([
            {'username': 'daichi\\semenov', 'full_name': 'Семёнов Иван'},
            {'username': 'daichi\\petrov', 'full_name': 'Петров Пётр'},
        ], ensure_ascii=False).encode('utf-8'))

    def test_catalog_cached_across_different_queries_and_dc_passed(self):
        with patch('base.directory_search.subprocess.run', return_value=self.result()) as run:
            self.assertEqual(search_accounts('Семенов')[0]['username'], 'daichi\\semenov')
            self.assertEqual(search_accounts('Петров')[0]['username'], 'daichi\\petrov')
            self.assertEqual(search_accounts('неизвестный'), [])
            self.assertEqual(run.call_count, 1)
            self.assertEqual(run.call_args.args[0][-3:-1], ['DAICHI', 'dc.example.test'])
            self.assertEqual(run.call_args.kwargs['timeout'], 10)

    def test_timeout_returns_actionable_message_and_releases_lock(self):
        with patch('base.directory_search.subprocess.run', side_effect=subprocess.TimeoutExpired('worker', 10)):
            with self.assertRaisesRegex(RuntimeError, '10 секунд'):
                search_accounts('Семенов')
        self.assertFalse(_catalog_lock.locked())
        with patch('base.directory_search.subprocess.run', return_value=self.result()):
            self.assertEqual(len(search_accounts('Семенов')), 1)

    def test_parallel_refresh_does_not_start_second_worker(self):
        _catalog_lock.acquire()
        try:
            with patch('base.directory_search.subprocess.run') as run:
                with self.assertRaisesRegex(RuntimeError, 'уже загружается'):
                    search_accounts('Семенов')
                run.assert_not_called()
        finally:
            _catalog_lock.release()

    def test_failed_worker_is_not_cached(self):
        with patch('base.directory_search.subprocess.run', return_value=SimpleNamespace(returncode=1)):
            with self.assertRaisesRegex(RuntimeError, 'DOMAIN'):
                search_accounts('Семенов')
        with patch('base.directory_search.subprocess.run', return_value=self.result()) as run:
            self.assertTrue(search_accounts('Семенов'))
            run.assert_called_once()

    def test_latin_login_uses_targeted_lookup_and_cache_is_per_account(self):
        with patch('base.directory_search.subprocess.run', return_value=self.result()) as run:
            self.assertEqual(search_accounts('semenov')[0]['username'], 'daichi\\semenov')
            self.assertEqual(run.call_args.args[0][-1], 'semenov')
            search_accounts('DAICHI\\semenov')
            self.assertEqual(run.call_count, 1)
            search_accounts('petrov')
            self.assertEqual(run.call_count, 2)
            self.assertEqual(run.call_args.args[0][-1], 'petrov')

    @override_settings(AD_UPN_SUFFIX='example.test')
    def test_dns_domain_used_when_no_controller_override(self):
        with patch('base.directory_search.os', SimpleNamespace(name='nt', environ={})):
            with patch('base.directory_search.subprocess.run', return_value=self.result()) as run:
                search_accounts('semenov')
                self.assertEqual(run.call_args.args[0][-2], 'example.test')

    def test_native_exact_lookup_skips_enumeration_and_frees_buffer(self):
        import ctypes
        from ctypes import wintypes
        from unittest.mock import MagicMock
        from .directory_search import _read_windows_accounts
        class Info(ctypes.Structure):
            _fields_ = [('name', wintypes.LPWSTR), ('full_name', wintypes.LPWSTR), ('comment', wintypes.LPWSTR), ('flags', wintypes.DWORD), ('user_id', wintypes.DWORD)]
        record = Info('krasnoperov', 'Красноперов Иван', '', 0, 123)
        api = MagicMock()
        def lookup(server, account, level, target):
            self.assertEqual(server.value, '\\\\dc.example.test')
            self.assertEqual(account, 'krasnoperov')
            ctypes.cast(target, ctypes.POINTER(ctypes.c_void_p))[0] = ctypes.cast(ctypes.pointer(record), ctypes.c_void_p)
            return 0
        api.NetUserGetInfo.side_effect = lookup
        with patch('base.directory_search.ctypes.WinDLL', return_value=api, create=True):
            self.assertEqual(_read_windows_accounts('DAICHI', 'dc.example.test', 'krasnoperov'), [{'username': 'daichi\\krasnoperov', 'full_name': 'Красноперов Иван'}])
            record.flags = 2
            self.assertEqual(_read_windows_accounts('DAICHI', 'dc.example.test', 'krasnoperov'), [])
        api.NetUserEnum.assert_not_called()
        api.NetGetDCName.assert_not_called()
        self.assertEqual(api.NetApiBufferFree.call_count, 2)
