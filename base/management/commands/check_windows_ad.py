"""Interactive Windows validation without LDAP, a web server, or DB changes."""
from getpass import getpass
from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.core.management.base import BaseCommand, CommandError
from base.windows_password import windows_password_identity


class Command(BaseCommand):
    help = 'Проверить доменный логин через Windows без LDAP. Пароль вводится скрыто, пользователь портала не создаётся.'

    def add_arguments(self, parser):
        parser.add_argument('--username', required=True, help='DOMAIN\\login или user@domain')

    def handle(self, *args, **options):
        if not settings.AD_NETBIOS_DOMAIN or not settings.AD_UPN_SUFFIX:
            raise CommandError('Задайте PORTAL_AD_NETBIOS_DOMAIN и PORTAL_AD_UPN_SUFFIX.')
        password = getpass('Доменный пароль (ввод скрыт): ')
        try:
            identity = windows_password_identity(options['username'], password)
        except PermissionDenied:
            raise CommandError('Windows отклонила вход. Проверьте логин, пароль, состояние учётной записи и право сетевого входа на сервер.') from None
        except Exception as error:
            raise CommandError('Проверка Windows недоступна: ' + type(error).__name__ + '. Запустите команду на Windows-сервере в домене.') from None
        finally:
            password = None
        self.stdout.write(self.style.SUCCESS('Windows подтвердила учётную запись: ' + identity['username']))
        self.stdout.write('LDAP/LDAPS не использовались. Пользователь портала и его права не изменены.')
