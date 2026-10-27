from pathlib import Path
from django.conf import settings
from django.core.management.base import CommandError
from django.core.management.commands.migrate import Command as DjangoMigrate
from django.db import connections
from django.db.migrations.executor import MigrationExecutor
from model_purch.services.backups import backup_sqlite
from model_purch.services.migration_safety import metadata_only_plan


class Command(DjangoMigrate):
    """Keep the standard migrate command and snapshot before schema changes."""

    def handle(self, *args, **options):
        connection = connections[options['database']]
        name = str(connection.settings_dict['NAME'])
        read_only = any(options.get(flag) for flag in ('plan', 'check_unapplied'))
        if not read_only and connection.vendor == 'sqlite' and not name.startswith(('file:', ':memory:')) and Path(name).is_file():
            executor = MigrationExecutor(connection)
            # A backup is also required for explicitly requested reverse/fake migrations.
            plan = executor.migration_plan(executor.loader.graph.leaf_nodes())
            explicit = bool(options.get('app_label') or options.get('fake') or options.get('fake_initial') or options.get('run_syncdb'))
            label_only = not explicit and metadata_only_plan(executor, plan)
            if label_only:
                self.stdout.write('Только подписи и подсказки полей: структура и данные не меняются; полная резервная копия не требуется.')
            if explicit or plan and not label_only:
                size_mb = Path(name).stat().st_size / (1024 * 1024)
                self.stdout.write(f'Есть изменения схемы. Создание и проверка резервной копии SQLite ({size_mb:.1f} МБ)…')
                self.stdout.flush()
                try:
                    def report(message):
                        self.stdout.write(message)
                        self.stdout.flush()
                    path = backup_sqlite(name, settings.BASE_DIR / '.local' / 'backups', notify=report)
                except Exception as error:
                    raise CommandError(f'Миграции остановлены: резервная копия не создана: {error}') from error
                self.stdout.write(f'Резервная копия перед миграциями: {path}')
        return super().handle(*args, **options)
