from pathlib import Path
from django.conf import settings
from django.core.management.base import CommandError
from django.core.management.commands.migrate import Command as DjangoMigrate
from django.db import connections
from django.db.migrations.executor import MigrationExecutor
from model_purch.services.backups import backup_sqlite


class Command(DjangoMigrate):
    """Keep the standard migrate command and snapshot before schema changes."""

    def handle(self, *args, **options):
        connection = connections[options['database']]
        name = str(connection.settings_dict['NAME'])
        read_only = any(options.get(flag) for flag in ('plan', 'check_unapplied'))
        if not read_only and connection.vendor == 'sqlite' and not name.startswith(('file:', ':memory:')) and Path(name).is_file():
            executor = MigrationExecutor(connection)
            # A backup is also required for explicitly requested reverse/fake migrations.
            if options.get('app_label') or executor.migration_plan(executor.loader.graph.leaf_nodes()):
                try:
                    path = backup_sqlite(name, settings.BASE_DIR / '.local' / 'backups')
                except Exception as error:
                    raise CommandError(f'Миграции остановлены: резервная копия не создана: {error}') from error
                self.stdout.write(f'Резервная копия перед миграциями: {path}')
        return super().handle(*args, **options)
