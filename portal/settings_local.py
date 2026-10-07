from .local_config import database_path
"""Local/cloud development without corporate database access."""
from .settings import *  # noqa: F403

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': database_path(BASE_DIR),
    }
}
MS_SQL_CONN_STR = os.environ.get('MS_SQL_CONN_STR') or None
ENABLE_LEGACY_DASH = False

MEDIA_ROOT = BASE_DIR / '.local' / 'media'
