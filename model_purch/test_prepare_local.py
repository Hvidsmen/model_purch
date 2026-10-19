import io
import json
import os
import sqlite3
import tempfile
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch
from django.test import SimpleTestCase
from scripts import prepare_local


class PrepareLocalTests(SimpleTestCase):
    def test_normal_start_skips_full_scan_and_prepares_static_in_same_process(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / 'db.sqlite3'
            with sqlite3.connect(database) as connection:
                connection.execute('CREATE TABLE sample (id INTEGER)')
            with patch.object(prepare_local, 'ROOT', root), patch('sys.argv', ['prepare_local.py', '--database', str(database)]), patch.dict(os.environ), patch('django.setup'), patch(
                'django.core.management.call_command',
            ) as command, patch('model_purch.models.ScenarioModel.objects.count', return_value=2), patch.object(prepare_local, 'check_database') as full_check, redirect_stdout(io.StringIO()) as output:
                prepare_local.main()
            full_check.assert_not_called()
            self.assertEqual([call.args[0] for call in command.call_args_list], ['check', 'migrate', 'collectstatic'])
            self.assertFalse(command.call_args_list[1].kwargs['interactive'])
            self.assertFalse(command.call_args_list[2].kwargs['interactive'])
            self.assertEqual(json.loads((root / '.local/config.json').read_text())['database_path'], str(database))
            self.assertIn('Проверка и применение миграций', output.getvalue())
            self.assertIn('Обновление статических файлов', output.getvalue())

    def test_requested_full_check_rejects_corrupt_database_before_django(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / 'broken.sqlite3'
            database.write_text('Not SQLite')
            with patch.object(prepare_local, 'ROOT', root), patch('sys.argv', ['prepare_local.py', '--database', str(database), '--check-database']), patch('django.setup') as setup, redirect_stdout(io.StringIO()), self.assertRaises(sqlite3.DatabaseError):
                prepare_local.main()
            setup.assert_not_called()
            self.assertFalse((root / '.local/config.json').exists())

    def test_static_preparation_failure_preserves_previous_database_selection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / 'db.sqlite3'
            database.touch()
            config = root / '.local/config.json'
            config.parent.mkdir()
            config.write_text('{"database_path": "previous.sqlite3"}')
            def command(name, **kwargs):
                if name == 'collectstatic':
                    raise RuntimeError('static failed')
            with patch.object(prepare_local, 'ROOT', root), patch('sys.argv', ['prepare_local.py', '--database', str(database)]), patch.dict(os.environ), patch('django.setup'), patch('django.core.management.call_command', side_effect=command), redirect_stdout(io.StringIO()), self.assertRaisesRegex(RuntimeError, 'static failed'):
                prepare_local.main()
            self.assertEqual(json.loads(config.read_text())['database_path'], 'previous.sqlite3')

    def test_full_check_accepts_valid_database(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / 'db.sqlite3'
            with sqlite3.connect(database) as connection:
                connection.execute('CREATE TABLE sample (id INTEGER)')
            prepare_local.check_database(database)
            # Closing the connection is essential for Windows file access.
            database.unlink()
