"""Validate AD credentials through Windows LogonUser; no LDAP or impersonation."""
import ctypes
import os
from ctypes import wintypes
from django.conf import settings
from django.core.exceptions import PermissionDenied


def _load_api():
    if os.name != 'nt':
        raise RuntimeError('Windows password authentication requires a Windows host')
    security = ctypes.WinDLL('advapi32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    security.LogonUserW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR,
                                    wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    security.LogonUserW.restype = wintypes.BOOL
    security.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID,
                                            wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    security.GetTokenInformation.restype = wintypes.BOOL
    security.LookupAccountSidW.argtypes = [wintypes.LPCWSTR, wintypes.LPVOID, wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD), wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD)]
    security.LookupAccountSidW.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    return security, kernel


def _token_account(security, token):
    needed = wintypes.DWORD()
    # TokenUser (1) begins with SID_AND_ATTRIBUTES; its first member is PSID.
    security.GetTokenInformation(token, 1, None, 0, ctypes.byref(needed))
    if needed.value < ctypes.sizeof(ctypes.c_void_p):
        raise PermissionDenied
    buffer = ctypes.create_string_buffer(needed.value)
    if not security.GetTokenInformation(token, 1, buffer, needed.value, ctypes.byref(needed)):
        raise PermissionDenied
    sid = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p))[0]
    name_size, domain_size, kind = wintypes.DWORD(), wintypes.DWORD(), wintypes.DWORD()
    security.LookupAccountSidW(None, sid, None, ctypes.byref(name_size), None, ctypes.byref(domain_size), ctypes.byref(kind))
    if not name_size.value or not domain_size.value:
        raise PermissionDenied
    name = ctypes.create_unicode_buffer(name_size.value)
    domain = ctypes.create_unicode_buffer(domain_size.value)
    if not security.LookupAccountSidW(None, sid, name, ctypes.byref(name_size), domain, ctypes.byref(domain_size), ctypes.byref(kind)):
        raise PermissionDenied
    if kind.value != 1:  # SidTypeUser only, never groups or machine identities.
        raise PermissionDenied
    return domain.value, name.value


def windows_password_identity(username, password):
    if not password or '\x00' in password or '\x00' in username:
        raise PermissionDenied
    if '\\' in username:
        parts = username.split('\\')
        if len(parts) != 2 or parts[0].casefold() != settings.AD_NETBIOS_DOMAIN.casefold():
            raise PermissionDenied
        domain, account = settings.AD_NETBIOS_DOMAIN, parts[1]
    else:
        parts = username.split('@')
        if len(parts) != 2 or parts[1].casefold() != settings.AD_UPN_SUFFIX.casefold():
            raise PermissionDenied
        domain, account = None, username
    if not account or any(character.isspace() or ord(character) < 32 for character in account):
        raise PermissionDenied
    security, kernel = _load_api()
    token = wintypes.HANDLE()
    # NETWORK=3 validates credentials; NEW_CREDENTIALS=9 would not validate
    # them immediately and must never be used here. No impersonation follows.
    if not security.LogonUserW(account, domain, password, 3, 0, ctypes.byref(token)):
        raise PermissionDenied
    try:
        actual_domain, actual_name = _token_account(security, token)
        if actual_domain.casefold() != settings.AD_NETBIOS_DOMAIN.casefold():
            raise PermissionDenied
        canonical = (actual_domain + '\\' + actual_name).casefold()
        if not actual_name or '\\' in actual_name or len(canonical) > 150:
            raise PermissionDenied
        return {'username': canonical}
    finally:
        kernel.CloseHandle(token)
