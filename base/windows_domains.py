"""Discover domains in the Windows AD forest, without LDAP or passwords."""
import ctypes
import json
import os
import sys
from ctypes import wintypes
from django.conf import settings
from django.core.cache import cache


def _read_forest_domains(server=''):
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
        # DS_DOMAIN_IN_FOREST: do not grant login to arbitrary external trusts.
        status = api.DsEnumerateDomainTrustsW(server or None, 1, ctypes.byref(buffer), ctypes.byref(count))
        if status:
            raise RuntimeError('Windows не смогла получить домены леса AD.')
        rows = ctypes.cast(buffer, ctypes.POINTER(DomainTrust))
        return [{'netbios': rows[i].netbios, 'dns': rows[i].dns or ''}
                for i in range(count.value) if rows[i].netbios and rows[i].flags & 1]
    finally:
        if buffer:
            api.NetApiBufferFree(buffer)


def domain_map(*, background=False):
    result = {settings.AD_NETBIOS_DOMAIN.casefold(): settings.AD_UPN_SUFFIX.casefold()}
    if os.name != 'nt' or not getattr(settings, 'AD_FOREST_DISCOVERY', True):
        return result
    server = os.environ.get('PORTAL_AD_DIRECTORY_SERVER', '').strip()
    key = 'portal-ad-forest:' + settings.AD_NETBIOS_DOMAIN.casefold() + ':' + server.casefold()
    rows = cache.get(key)
    if rows is None:
        from .directory_search import _background_catalog, _load_catalog
        rows = (_background_catalog if background else _load_catalog)('__forest__', server, '', key)
    for row in rows:
        if row.get('netbios') and row.get('dns'):
            result[row['netbios'].casefold()] = row['dns'].casefold()
    return result


if __name__ == '__main__':
    try:
        sys.stdout.buffer.write(json.dumps(_read_forest_domains(sys.argv[1]), ensure_ascii=False).encode('utf-8'))
    except Exception:
        sys.exit(1)
