"""Discover domains in the Windows AD forest and trusted domains, without LDAP or passwords."""
import ctypes
import json
import os
import sys
from ctypes import wintypes
from django.conf import settings
from django.core.cache import cache
from .windows_directory import WindowsDirectoryError


def _read_forest_domains(server='', include_trusted=True):
    api = ctypes.WinDLL('Netapi32.dll')
    class DomainTrust(ctypes.Structure):
        _fields_ = [('netbios', wintypes.LPWSTR), ('dns', wintypes.LPWSTR),
                    ('flags', wintypes.ULONG), ('parent', wintypes.ULONG),
                    ('trust_type', wintypes.ULONG), ('attributes', wintypes.ULONG),
                    ('sid', ctypes.c_void_p), ('guid', ctypes.c_byte * 16)]
    api.DsEnumerateDomainTrustsW.argtypes = [wintypes.LPCWSTR, wintypes.ULONG, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.ULONG)]
    api.DsEnumerateDomainTrustsW.restype = wintypes.DWORD
    api.NetApiBufferFree.argtypes = [ctypes.c_void_p]
    buffer, count = ctypes.c_void_p(), wintypes.ULONG()
    try:
        # Forest members plus direct outbound/inbound trusts reported by Windows.
        flags = 1 | 2 | 32 if include_trusted else 1
        status = api.DsEnumerateDomainTrustsW(server or None, flags, ctypes.byref(buffer), ctypes.byref(count))
        if status:
            raise WindowsDirectoryError('DsEnumerateDomainTrustsW', status)
        rows = ctypes.cast(buffer, ctypes.POINTER(DomainTrust))
        return [{'netbios': rows[i].netbios, 'dns': rows[i].dns or ''}
                for i in range(count.value) if rows[i].netbios and rows[i].flags & flags]
    finally:
        if buffer:
            api.NetApiBufferFree(buffer)


def domain_map(*, background=False):
    result = {settings.AD_NETBIOS_DOMAIN.casefold(): settings.AD_UPN_SUFFIX.casefold()}
    if os.name != 'nt' or not getattr(settings, 'AD_FOREST_DISCOVERY', True):
        return result
    server = os.environ.get('PORTAL_AD_DIRECTORY_SERVER', '').strip()
    mode = 'trusted' if settings.AD_TRUSTED_DOMAIN_DISCOVERY else 'forest'
    key = 'portal-ad-domains-v2:' + settings.AD_NETBIOS_DOMAIN.casefold() + ':' + server.casefold() + ':' + mode
    rows = cache.get(key)
    if rows is None:
        from .directory_search import _background_catalog, _load_catalog
        rows = (_background_catalog if background else _load_catalog)('__forest__', server, mode, key)
    for row in rows:
        if row.get('netbios'):
            result[row['netbios'].casefold()] = (row.get('dns') or '').casefold()
    return result


if __name__ == '__main__':
    try:
        sys.stdout.buffer.write(json.dumps(_read_forest_domains(sys.argv[1], len(sys.argv) > 2 and sys.argv[2] == 'trusted'), ensure_ascii=False).encode('utf-8'))
    except WindowsDirectoryError as error:
        sys.stdout.buffer.write(json.dumps({'error': {'stage': error.stage, 'code': error.code}}).encode('utf-8'))
        sys.exit(1)
    except Exception:
        sys.exit(1)
