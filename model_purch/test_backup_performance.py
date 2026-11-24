import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock,patch
from .services.backups import backup_sqlite


class BackupPerformanceTests(TestCase):
    def test_optimized_backup_preserves_wal_data_and_reports_each_phase(self):
        with TemporaryDirectory() as directory:
            root=Path(directory); source=root/'db.sqlite3'
            with sqlite3.connect(source) as db:
                db.execute('PRAGMA journal_mode=WAL')
                db.execute('PRAGMA wal_autocheckpoint=0')
                db.execute('CREATE TABLE items (value TEXT)')
                db.executemany('INSERT INTO items VALUES (?)', [('тест'*10000,)]*40)
                db.commit()
                self.assertTrue(source.with_name('db.sqlite3-wal').exists())
                logs=[]; destination=backup_sqlite(source,root/'backups',notify=logs.append)
                with sqlite3.connect(destination) as restored:
                    self.assertEqual(restored.execute('SELECT count(*) FROM items').fetchone()[0],40)
                    self.assertEqual(restored.execute('PRAGMA quick_check').fetchone()[0],'ok')
            self.assertTrue(any('copy complete in' in s for s in logs))
            self.assertTrue(any('integrity check complete in' in s for s in logs))

    def test_low_space_fails_before_opening_or_removing_backups(self):
        with TemporaryDirectory() as directory:
            root=Path(directory);source=root/'db.sqlite3';source.touch()
            with patch('model_purch.services.backups.shutil.disk_usage',return_value=SimpleNamespace(free=0)),patch('model_purch.services.backups.sqlite3.connect') as connect,patch('model_purch.services.backups.prune_backups') as prune:
                with self.assertRaisesRegex(RuntimeError,'Недостаточно места'):backup_sqlite(source,root/'backups')
                connect.assert_not_called();prune.assert_not_called()

    def test_full_validation_still_rejects_a_failed_copy(self):
        with TemporaryDirectory() as directory:
            root=Path(directory);source=root/'db.sqlite3';source.touch()
            reader,target=Mock(),Mock()
            target.execute.return_value.fetchone.return_value=('corrupt page',)
            with patch('model_purch.services.backups.sqlite3.connect',side_effect=[reader,target]),patch('model_purch.services.backups.prune_backups') as prune:
                with self.assertRaisesRegex(RuntimeError,'corrupt page'):backup_sqlite(source,root/'backups',notify=lambda _:None)
                target.execute.assert_any_call('PRAGMA quick_check')
                prune.assert_not_called()
            self.assertEqual(list((root/'backups').iterdir()),[])
