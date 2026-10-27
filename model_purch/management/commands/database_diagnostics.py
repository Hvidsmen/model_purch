import json
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connections
from model_purch.services.database_diagnostics import diagnose_database


class Command(BaseCommand):
    help = 'Диагностика размера SQLite, свободного места и таблиц без изменения базы.'

    def add_arguments(self, parser):
        parser.add_argument('--database', default='default')
        parser.add_argument('--tables', action='store_true', help='Прочитать распределение страниц по таблицам и индексам.')
        parser.add_argument('--timeout', type=int, default=15)

    def handle(self, *args, **options):
        connection = connections[options['database']]
        if connection.vendor != 'sqlite':
            raise CommandError('Команда предназначена для SQLite.')
        if not 1 <= options['timeout'] <= 600:
            raise CommandError('Лимит времени должен быть от 1 до 600 секунд.')
        try:
            report = diagnose_database(connection.settings_dict['NAME'], options['tables'], options['timeout'], backup_directory=settings.BASE_DIR / '.local' / 'backups')
        except Exception as error:
            raise CommandError(f'Не удалось получить диагностику: {error}') from error
        self.stdout.write(json.dumps(report, ensure_ascii=False, indent=2))
