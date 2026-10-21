"""Resolve an exact Windows principal through LSA name/SID APIs, without LDAP."""
import ctypes
from ctypes import wintypes


class WindowsDirectoryError(RuntimeError):
    def __init__(self, stage, code):
        self.stage, self.code = stage, int(code)
        hints = {5: 'Нет доступа у учётной записи службы портала.', 53: 'Сетевой путь недоступен.',
                 1311: 'Нет доступных серверов входа.', 1355: 'Контроллер домена не найден.',
                 1722: 'RPC-сервер недоступен.', 1789: 'Ошибка доверительных отношений с доменом.'}
        super().__init__(f'{stage}: ошибка Windows {self.code}. {hints.get(self.code, "Проверьте доступ Windows к домену.")}')


def lookup_account(domain, account):
    security = ctypes.WinDLL('advapi32.dll', use_last_error=True)
    security.LookupAccountNameW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPVOID,
        ctypes.POINTER(wintypes.DWORD), wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD)]
    security.LookupAccountNameW.restype = wintypes.BOOL
    security.LookupAccountSidW.argtypes = [wintypes.LPCWSTR, wintypes.LPVOID, wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD), wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD)]
    security.LookupAccountSidW.restype = wintypes.BOOL
    qualified = domain + '\\' + account
    sid_size, domain_size, kind = wintypes.DWORD(), wintypes.DWORD(), wintypes.DWORD()
    security.LookupAccountNameW(None, qualified, None, ctypes.byref(sid_size), None, ctypes.byref(domain_size), ctypes.byref(kind))
    error = ctypes.get_last_error()
    if error == 1332:  # ERROR_NONE_MAPPED: absence, not a network error.
        return []
    if error != 122 or not sid_size.value or not domain_size.value:
        raise WindowsDirectoryError('LookupAccountNameW', error)
    sid = ctypes.create_string_buffer(sid_size.value)
    resolved_domain = ctypes.create_unicode_buffer(domain_size.value)
    if not security.LookupAccountNameW(None, qualified, sid, ctypes.byref(sid_size), resolved_domain, ctypes.byref(domain_size), ctypes.byref(kind)):
        raise WindowsDirectoryError('LookupAccountNameW', ctypes.get_last_error())
    if kind.value != 1:  # SidTypeUser; never import a group/computer as a user.
        return []
    name_size, canonical_domain_size = wintypes.DWORD(), wintypes.DWORD()
    security.LookupAccountSidW(None, sid, None, ctypes.byref(name_size), None, ctypes.byref(canonical_domain_size), ctypes.byref(kind))
    error = ctypes.get_last_error()
    if error != 122 or not name_size.value or not canonical_domain_size.value:
        raise WindowsDirectoryError('LookupAccountSidW', error)
    name = ctypes.create_unicode_buffer(name_size.value)
    canonical_domain = ctypes.create_unicode_buffer(canonical_domain_size.value)
    if not security.LookupAccountSidW(None, sid, name, ctypes.byref(name_size), canonical_domain, ctypes.byref(canonical_domain_size), ctypes.byref(kind)):
        raise WindowsDirectoryError('LookupAccountSidW', ctypes.get_last_error())
    if kind.value != 1 or canonical_domain.value.casefold() != domain.casefold():
        return []
    username = (canonical_domain.value + '\\' + name.value).casefold()
    return [{'username': username, 'full_name': _display_name(username)}]


def _display_name(username):
    try:
        api = ctypes.WinDLL('secur32.dll', use_last_error=True)
        api.TranslateNameW.argtypes = [wintypes.LPCWSTR, ctypes.c_int, ctypes.c_int, wintypes.LPWSTR, ctypes.POINTER(wintypes.ULONG)]
        api.TranslateNameW.restype = wintypes.BOOL
        size = wintypes.ULONG()
        api.TranslateNameW(username, 2, 3, None, ctypes.byref(size))
        if not 0 < size.value <= 1024:
            return ''
        buffer = ctypes.create_unicode_buffer(size.value)
        return buffer.value if api.TranslateNameW(username, 2, 3, buffer, ctypes.byref(size)) else ''
    except (OSError, AttributeError):
        return ''
