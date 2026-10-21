"""Verify DC TLS without requesting or sending a password."""
import socket
import ssl
from urllib.parse import urlsplit
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = 'Проверить DNS, порт LDAPS и сертификат контроллера домена без пароля.'

    def handle(self, *args, **options):
        endpoint = urlsplit(settings.AD_LDAPS_URL)
        if endpoint.scheme != 'ldaps' or not endpoint.hostname or endpoint.username or endpoint.password or endpoint.path not in ('', '/') or endpoint.query or endpoint.fragment:
            raise CommandError('Задайте PORTAL_AD_LDAPS_URL в формате ldaps://имя-контроллера:636 без логина и пароля.')
        try:
            context = ssl.create_default_context(cafile=settings.AD_CA_CERT_FILE or None)
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            with socket.create_connection((endpoint.hostname, endpoint.port or 636), timeout=settings.AD_CONNECT_TIMEOUT) as raw:
                with context.wrap_socket(raw, server_hostname=endpoint.hostname) as secured:
                    protocol = secured.version()
        except (OSError, ValueError) as error:
            raise CommandError('Не удалось проверить LDAPS: ' + type(error).__name__ + '. Проверьте DNS, порт, имя в сертификате и публичную цепочку CA.') from None
        self.stdout.write(self.style.SUCCESS(f'LDAPS TLS доступен: {endpoint.hostname}:{endpoint.port or 636}, {protocol}. Сертификат и имя сервера проверены.'))
        self.stdout.write('Пароль не отправлялся. Это не проверка входа пользователя или прав поиска в каталоге.')
