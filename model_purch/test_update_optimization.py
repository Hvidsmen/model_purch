from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch
import sqlite3
from django.db import migrations, models
from django.db.migrations.state import ModelState, ProjectState
from django.test import SimpleTestCase
from model_purch.services.migration_safety import metadata_only_plan
from model_purch.services.database_diagnostics import diagnose_database
from model_purch.management.commands.migrate import Command


class MetadataMigrationTests(SimpleTestCase):
    def executor(self, operations):
        state = ProjectState()
        state.add_model(ModelState('app', 'Item', [('id', models.AutoField(primary_key=True)),
                                                 ('price', models.FloatField(default=0, verbose_name='Old'))]))
        migration = migrations.Migration('new', 'app')
        migration.operations = operations
        executor = Mock()
        executor._create_project_state.return_value = state
        return executor, [(migration, False)]

    def test_label_change_is_safe_but_schema_data_and_custom_operations_require_backup(self):
        label = migrations.AlterField('item', 'price', models.FloatField(default=0, verbose_name='New', help_text='Hint'))
        executor, plan = self.executor([label])
        self.assertTrue(metadata_only_plan(executor, plan))
        for operation in (
            migrations.AlterField('item', 'price', models.IntegerField(default=0)),
            migrations.AlterField('item', 'price', models.FloatField(default=1)),
            migrations.AlterField('item', 'price', models.FloatField(default=0, null=True)),
            migrations.RunSQL('SELECT 1'), migrations.RunPython(migrations.RunPython.noop),
            migrations.AddField('item', 'extra', models.IntegerField(default=0)),
        ):
            with self.subTest(operation=operation):
                executor, plan = self.executor([label, operation])
                self.assertFalse(metadata_only_plan(executor, plan))
        executor, plan = self.executor([label])
        self.assertFalse(metadata_only_plan(executor, [(plan[0][0], True)]))
        self.assertFalse(metadata_only_plan(executor, []))
        class CustomAlter(migrations.AlterField):
            pass
        executor, plan = self.executor([CustomAlter('item', 'price', models.FloatField(default=0))])
        self.assertFalse(metadata_only_plan(executor, plan))

    def test_command_retains_backup_for_explicit_or_structural_migrations(self):
        with TemporaryDirectory() as folder:
            database = Path(folder) / 'db.sqlite3'
            database.touch()
            connection = SimpleNamespace(vendor='sqlite', settings_dict={'NAME': str(database)})
            for metadata, explicit, expected in ((True, False, False), (False, False, True), (True, True, True)):
                executor, plan = self.executor([migrations.AlterField('item', 'price', models.FloatField(default=0, help_text='Hint'))])
                executor.migration_plan.return_value = plan
                with patch('model_purch.management.commands.migrate.connections', {'default': connection}), \
                     patch('model_purch.management.commands.migrate.MigrationExecutor', return_value=executor), \
                     patch('model_purch.management.commands.migrate.metadata_only_plan', return_value=metadata), \
                     patch('model_purch.management.commands.migrate.backup_sqlite') as backup, \
                     patch('django.core.management.commands.migrate.Command.handle'):
                    Command().handle(database='default', app_label='app' if explicit else None)
                self.assertEqual(backup.called, expected)


class DatabaseDiagnosticsTests(SimpleTestCase):
    def test_storage_report_includes_tables_indexes_and_preserves_database(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / 'db.sqlite3'
            with sqlite3.connect(path) as db:
                db.execute('CREATE TABLE history (value TEXT)')
                db.execute('CREATE INDEX history_value ON history(value)')
                db.executemany('INSERT INTO history VALUES (?)', [('value' * 100,)] * 200)
                db.commit()
            original = path.read_bytes()
            report = diagnose_database(path, include_tables=True, backup_directory=Path(folder) / 'not-created' / 'backups')
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(report['database_bytes'], len(original))
            self.assertGreater(report['disk_free_bytes'], 0)
            self.assertGreater(report['backup_disk_free_bytes'], 0)
            self.assertTrue(report['backup_on_same_filesystem'])
            self.assertFalse((Path(folder) / 'not-created').exists())
            self.assertEqual(report['page_size'] * report['page_count'], len(original))
            self.assertEqual(report['table_details'], 'complete')
            self.assertIn('history', [row['name'] for row in report['objects']])
            self.assertIn('history_value', [row['name'] for row in report['objects']])
            self.assertEqual(diagnose_database(path)['objects'], [])
