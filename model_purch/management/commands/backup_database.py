from pathlib import Path
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connections
from model_purch.services.backups import backup_sqlite


class Command(BaseCommand):
    help = 'Создать проверенную резервную копию активной SQLite-базы.'

    def add_arguments(self, parser):
        parser.add_argument('--directory', default=str(settings.BASE_DIR / '.local' / 'backups'))
        parser.add_argument('--database', default='default')

    def handle(self, *args, **options):
        connection = connections[options['database']]
        if connection.vendor != 'sqlite' or str(connection.settings_dict['NAME']).startswith(('file:', ':memory:')):
            raise CommandError('Команда предназначена для SQLite-файла.')
        try:
            path = backup_sqlite(connection.settings_dict['NAME'], Path(options['directory']))
        except Exception as error:
            raise CommandError(str(error)) from error
        self.stdout.write(self.style.SUCCESS(f'Резервная копия: {path}'))
