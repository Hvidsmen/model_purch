"""Read domain accounts through Windows NetAPI (RPC), without LDAP/passwords."""
import ctypes
import os
from ctypes import wintypes
from django.conf import settings


def search_accounts(query):
    if os.name != 'nt':
        raise RuntimeError('Поиск AD доступен на Windows-сервере, включённом в домен.')
    domain = settings.AD_NETBIOS_DOMAIN
    if not domain:
        raise RuntimeError('Задайте PORTAL_AD_NETBIOS_DOMAIN для поиска AD.')
    api = ctypes.WinDLL('Netapi32.dll')
    buffer = ctypes.c_void_p()
    server = ctypes.c_wchar_p()
    api.NetGetDCName.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p)]
    api.NetGetDCName.restype = wintypes.DWORD
    api.NetApiBufferFree.argtypes = [ctypes.c_void_p]
    if api.NetGetDCName(None, domain, ctypes.byref(buffer)):
        raise RuntimeError('Контроллер домена недоступен для учётной записи службы портала.')
    try:
        server.value = ctypes.wstring_at(buffer)
    finally:
        api.NetApiBufferFree(buffer)
    class UserInfo(ctypes.Structure):
        _fields_ = [('name', wintypes.LPWSTR), ('full_name', wintypes.LPWSTR), ('comment', wintypes.LPWSTR), ('flags', wintypes.DWORD), ('user_id', wintypes.DWORD)]
    api.NetUserEnum.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p), wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(ctypes.c_size_t)]
    api.NetUserEnum.restype = wintypes.DWORD
    resume = ctypes.c_size_t(0)
    results = []
    scanned = 0
    while True:
        read, total = wintypes.DWORD(), wintypes.DWORD()
        buffer = ctypes.c_void_p()
        status = api.NetUserEnum(server, 20, 2, ctypes.byref(buffer), 65536, ctypes.byref(read), ctypes.byref(total), ctypes.byref(resume))
        try:
            if status not in (0, 234):
                raise RuntimeError('Windows не разрешила чтение пользователей AD. Проверьте права учётной записи службы портала.')
            rows = ctypes.cast(buffer, ctypes.POINTER(UserInfo))
            for index in range(read.value):
                row = rows[index]
                username = (domain + '\\' + row.name).casefold()
                full_name = row.full_name or ''
                if not row.flags & 2 and query.casefold() in (username + ' ' + full_name).casefold():
                    results.append({'username': username, 'full_name': full_name})
                    if len(results) >= 50:
                        return results
            scanned += read.value
        finally:
            if buffer:
                api.NetApiBufferFree(buffer)
        if status == 0:
            return results
        if not read.value or scanned >= 50000:
            raise RuntimeError('Слишком большой каталог AD. Уточните настройки поиска у администратора домена.')
