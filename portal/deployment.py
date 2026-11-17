"""Non-secret, machine-local deployment profile shared by all entry points."""
import json
import os
from pathlib import Path


def site_settings(base_dir):
    path=Path(base_dir)/'.local/site-settings.json'
    if not path.exists():return {}
    try:
        data=json.loads(path.read_text(encoding='utf-8-sig'))
        if not isinstance(data,dict) or any(not isinstance(k,str) or not isinstance(v,str) for k,v in data.items()):raise ValueError('Expected string settings')
        return data
    except (OSError,ValueError) as exc:
        raise RuntimeError('Не удалось прочитать локальный профиль сайта .local/site-settings.json') from exc


def value(profile,name,default=''):
    # The explicit machine profile wins over stale inherited Task Scheduler env.
    return profile.get(name,os.environ.get(name,default))


def da_dev_profile(ca_file=''):
    return {
        'DJANGO_ALLOWED_HOSTS':'localhost,127.0.0.1,192.168.221.178,da-dev.daichi.ru',
        'PORTAL_HTTPS_PROXY':'true','PORTAL_TRUSTED_PROXIES':'192.168.221.9',
        'DJANGO_CSRF_TRUSTED_ORIGINS':'https://da-dev.daichi.ru',
        'DJANGO_SECURE_COOKIES':'true','DJANGO_SESSION_COOKIE_NAME':'da_portal_sessionid',
        'DJANGO_CSRF_COOKIE_NAME':'da_portal_csrftoken',
        'PORTAL_AD_AUTH':'true','PORTAL_AD_AUTH_METHOD':'ldaps',
        'PORTAL_AD_LDAPS_URL':'ldaps://dc-2tt.thermotrade.local:636',
        'PORTAL_AD_NETBIOS_DOMAIN':'TT','PORTAL_AD_UPN_SUFFIX':'thermotrade.local',
        'PORTAL_AD_SEARCH_BASE':'DC=thermotrade,DC=local','PORTAL_AD_CA_CERT_FILE':ca_file,
        'PORTAL_AD_SHORT_NAMES':'true','PORTAL_AD_ALLOW_HTTP':'false',
        'PORTAL_AD_TRUSTED_DOMAINS':'false','PORTAL_AD_FOREST_DISCOVERY':'false',
    }
