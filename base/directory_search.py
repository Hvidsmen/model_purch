"""Read domain accounts through Windows NetAPI (RPC), without LDAP/passwords."""
import ctypes
import os
import json
import re
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from django.core.cache import cache
from ctypes import wintypes
from django.conf import settings


_catalog_lock = threading.Lock()
_jobs_lock = threading.Lock()
_jobs = {}
_workers = ThreadPoolExecutor(max_workers=2, thread_name_prefix='portal-ad')


class DirectoryPending(RuntimeError):
    pass


def _load_catalog(domain, server, exact, key):
    try:
        process = subprocess.run([sys.executable, '-m', 'base.directory_search', domain, server, exact],
            capture_output=True, timeout=120, check=False)
    except subprocess.TimeoutExpired:
        raise RuntimeError('AD не ответила за 2 минуты. Проверьте доступ к контроллеру домена. Можно добавить DOMAIN\\login вручную.') from None
    if process.returncode:
        raise RuntimeError('Не удалось получить список AD. Проверьте контроллер домена и права учётной записи службы портала. Можно указать DOMAIN\\login вручную.')
    catalog = json.loads(process.stdout.decode('utf-8'))
    cache.set(key, catalog, timeout=300)
    return catalog


def _background_catalog(domain, server, exact, key):
    with _jobs_lock:
        job = _jobs.get(key)
        if job is not None and job.done():
            del _jobs[key]
            catalog = job.result()
            cache.set(key, catalog, timeout=300)
            return catalog
        if job is None:
            for old_key, old_job in list(_jobs.items()):
                if old_job.done():
                    del _jobs[old_key]
            if len(_jobs) >= 2:
                raise DirectoryPending('Ожидаем завершения текущей загрузки AD…')
            _jobs[key] = _workers.submit(_load_catalog, domain, server, exact, key)
    raise DirectoryPending('Загружаем каталог AD. Первая загрузка может занять до двух минут…')


def search_accounts(query, *, background=False):
    if os.name != 'nt':
        raise RuntimeError('Поиск AD доступен на Windows-сервере, включённом в домен.')
    domain = settings.AD_NETBIOS_DOMAIN
    server = os.environ.get('PORTAL_AD_DIRECTORY_SERVER', '').strip()
    if not domain:
        raise RuntimeError('Задайте PORTAL_AD_NETBIOS_DOMAIN для поиска AD.')
    key = 'portal-domain-users:' + domain.casefold() + ':' + server.casefold()
    catalog = cache.get(key)
    exact = query.strip()
    if '\\' in exact:
        prefix, exact = exact.split('\\', 1)
        if prefix.casefold() != domain.casefold():
            return []
    if '@' in exact:
        exact, suffix = exact.rsplit('@', 1)
        if suffix.casefold() != settings.AD_UPN_SUFFIX.casefold():
            return []
    exact = exact if re.fullmatch(r'[a-zA-Z0-9_.-]+', exact) else ''
    if catalog is None and exact:
        key += ':login:' + exact.casefold()
        catalog = cache.get(key)
    if catalog is None and background:
        catalog = _background_catalog(domain, server, exact, key)
    if catalog is None:
        if not _catalog_lock.acquire(timeout=0.2):
            raise DirectoryPending('Список AD уже загружается. Повторите поиск через несколько секунд.')
        try:
            catalog = cache.get(key)
            if catalog is None:
                catalog = _load_catalog(domain, server, exact, key)
        finally:
            _catalog_lock.release()
    normalize = lambda text: text.casefold().replace('ё', 'е')
    needle = normalize(exact or query)
    return [row for row in catalog if needle in normalize(row['username'] + ' ' + row['full_name'])][:50]


def _read_windows_accounts(domain, configured_server='', exact=''):
    api = ctypes.WinDLL('Netapi32.dll')
    buffer = ctypes.c_void_p()
    server = ctypes.c_wchar_p()
    api.NetGetDCName.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p)]
    api.NetGetDCName.restype = wintypes.DWORD
    api.NetApiBufferFree.argtypes = [ctypes.c_void_p]
    if configured_server:
        server.value = '\\\\' + configured_server.lstrip('\\')
    elif api.NetGetDCName(None, domain, ctypes.byref(buffer)):
        raise RuntimeError('Контроллер домена недоступен для учётной записи службы портала.')
    if not configured_server:
        try:
            server.value = ctypes.wstring_at(buffer)
        finally:
            api.NetApiBufferFree(buffer)
    class UserInfo(ctypes.Structure):
        _fields_ = [('name', wintypes.LPWSTR), ('full_name', wintypes.LPWSTR), ('comment', wintypes.LPWSTR), ('flags', wintypes.DWORD), ('user_id', wintypes.DWORD)]
    if exact:
        api.NetUserGetInfo.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p)]
        api.NetUserGetInfo.restype = wintypes.DWORD
        buffer = ctypes.c_void_p()
        status = api.NetUserGetInfo(server, exact, 20, ctypes.byref(buffer))
        try:
            if status == 2221:  # NERR_UserNotFound: distinguish absence from network failure.
                return []
            if status:
                raise RuntimeError('Windows не разрешила чтение учётной записи AD.')
            row = ctypes.cast(buffer, ctypes.POINTER(UserInfo)).contents
            return [] if row.flags & 2 else [{'username': (domain + '\\' + row.name).casefold(), 'full_name': row.full_name or ''}]
        finally:
            if buffer:
                api.NetApiBufferFree(buffer)
    api.NetUserEnum.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p), wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(ctypes.c_size_t)]
    api.NetUserEnum.restype = wintypes.DWORD
    resume = ctypes.c_size_t(0)
    results = []
    scanned = 0
    while True:
        read, total = wintypes.DWORD(), wintypes.DWORD()
        buffer = ctypes.c_void_p()
        status = api.NetUserEnum(server, 20, 2, ctypes.byref(buffer), 262144, ctypes.byref(read), ctypes.byref(total), ctypes.byref(resume))
        try:
            if status not in (0, 234):
                raise RuntimeError('Windows не разрешила чтение пользователей AD. Проверьте права учётной записи службы портала.')
            rows = ctypes.cast(buffer, ctypes.POINTER(UserInfo))
            for index in range(read.value):
                row = rows[index]
                username = (domain + '\\' + row.name).casefold()
                full_name = row.full_name or ''
                if not row.flags & 2:
                    results.append({'username': username, 'full_name': full_name})
            scanned += read.value
        finally:
            if buffer:
                api.NetApiBufferFree(buffer)
        if status == 0:
            return results
        if not read.value or scanned >= 50000:
            raise RuntimeError('Слишком большой каталог AD. Уточните настройки поиска у администратора домена.')


if __name__ == '__main__':
    try:
        accounts = _read_windows_accounts(sys.argv[1], sys.argv[2], sys.argv[3])
        sys.stdout.buffer.write(json.dumps(accounts, ensure_ascii=False).encode('utf-8'))
    except Exception:
        sys.exit(1)
