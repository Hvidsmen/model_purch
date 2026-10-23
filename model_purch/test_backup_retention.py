from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
from model_purch.services.backups import prune_backups, backup_sqlite
import sqlite3


class BackupRetentionTests(TestCase):
    def test_keeps_latest_three_only_for_requested_database(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            backups = root / 'backups'; backups.mkdir()
            files = []
            for day in range(1, 6):
                path = backups / f'db-202610{day:02d}T120000000000Z-1234abcd.sqlite3'
                path.touch(); files.append(path)
            untouched = ['db.sqlite3', 'manual-copy.sqlite3', 'db-old.sqlite3', 'other-20261001T120000000000Z-1234abcd.sqlite3', 'db.sqlite3-wal']
            for name in untouched: (backups / name).touch()
            removed = prune_backups(root / 'db.sqlite3', backups)
            self.assertEqual(set(removed), set(files[:2]))
            self.assertTrue(all(path.exists() for path in files[2:]))
            self.assertTrue(all((backups / name).exists() for name in untouched))

    def test_current_verified_backup_is_always_protected(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            protected = root / 'db-20010101T000000000000Z-1234abcd.sqlite3'; protected.touch()
            latest = root / 'db-20261001T000000000000Z-1234abcd.sqlite3'; latest.touch()
            self.assertEqual(prune_backups(root / 'db.sqlite3', root, keep=1, protected=[protected]), [])
            self.assertTrue(protected.exists())
            with self.assertRaises(ValueError): prune_backups(root / 'db.sqlite3', root, keep=0)

    def test_locked_file_does_not_break_cleanup(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            for day in range(1, 5): (root / f'db-202610{day:02d}T120000000000Z-1234abcd.sqlite3').touch()
            with patch.object(Path, 'unlink', side_effect=PermissionError('Locked')):
                self.assertEqual(prune_backups(root / 'db.sqlite3', root), [])

    def test_automatic_cleanup_runs_after_successful_verification_only(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / 'db.sqlite3'
            with sqlite3.connect(database) as db: db.execute('CREATE TABLE example (id int)')
            with patch('model_purch.services.backups.prune_backups', return_value=[]) as prune:
                path = backup_sqlite(database, root / 'backups')
                prune.assert_called_once_with(database.resolve(), (root / 'backups').resolve(), keep=3, protected=[path])
            with patch('model_purch.services.backups.sqlite3.connect', side_effect=RuntimeError('Failed')), patch('model_purch.services.backups.prune_backups') as prune:
                with self.assertRaises(RuntimeError): backup_sqlite(database, root / 'backups')
                prune.assert_not_called()
