"""SQL Server rehearsal only. Start explicitly; never selected by normal start.ps1."""
from copy import deepcopy
from .settings_local import *
from .sqlserver_trial_config import database_config,source_config

PORTAL_SQLSERVER_TRIAL=True
PORTAL_TRIAL_RUN_ID=os.environ.get('PORTAL_TRIAL_RUN_ID','')
DATABASES={'default':database_config(),'trial_source':source_config()}
DATABASE_ROUTERS=['portal.sqlserver_trial.SourceReadOnlyRouter']
DATABASE_CONNECTION_POOLING=False
DEBUG=False
ALLOWED_HOSTS=['localhost','127.0.0.1','[::1]']
SESSION_COOKIE_NAME='portal_sqltrial_session'
CSRF_COOKIE_NAME='portal_sqltrial_csrf'
SESSION_COOKIE_SECURE=False
CSRF_COOKIE_SECURE=False
SESSION_COOKIE_DOMAIN=None
CSRF_COOKIE_DOMAIN=None
MEDIA_ROOT=BASE_DIR/'.local'/'sqlserver-trial'/'media'
STATIC_ROOT=BASE_DIR/'.local'/'sqlserver-trial'/'static'
MS_SQL_CONN_STR=None
DWH_SQL_CONN_STR=None
WINDOWS_AUTH_ENABLED=False
AD_AUTH_ENABLED=False
AUTHENTICATION_BACKENDS=['django.contrib.auth.backends.ModelBackend']
MIDDLEWARE=[entry for entry in MIDDLEWARE if entry not in (
    'base.windows_auth.WindowsRemoteUserMiddleware','base.ad_auth.ActiveDirectoryMiddleware')]
MIDDLEWARE=['portal.sqlserver_trial.TrialIsolationMiddleware',*MIDDLEWARE]
TEMPLATES=deepcopy(TEMPLATES)
TEMPLATES[0]['OPTIONS']['context_processors'].append('portal.sqlserver_trial.context')
# Trial sessions and caches must never share the production portal's state.
CACHES={'default':{'BACKEND':'django.core.cache.backends.locmem.LocMemCache','LOCATION':'portal-sqlserver-trial'}}
EMAIL_BACKEND='django.core.mail.backends.dummy.EmailBackend'
