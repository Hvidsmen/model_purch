import sqlite3
import tempfile
from pathlib import Path
from unittest import TestCase
from scripts.prepare_local import configure_sqlite


class SqlitePreparationTests(TestCase):
    def test_wal_persists_and_reader_does_not_block_writer_commit(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'db.sqlite3'
            configure_sqlite(path)
            reader = sqlite3.connect(path, timeout=.1)
            writer = sqlite3.connect(path, timeout=.1)
            try:
                self.assertEqual(reader.execute('PRAGMA journal_mode').fetchone()[0], 'wal')
                writer.execute('CREATE TABLE sample (value INTEGER)')
                writer.execute('INSERT INTO sample VALUES (1)')
                writer.commit()
                reader.execute('BEGIN')
                self.assertEqual(reader.execute('SELECT value FROM sample').fetchone()[0], 1)
                writer.execute('UPDATE sample SET value=2')
                writer.commit()
                self.assertEqual(reader.execute('SELECT value FROM sample').fetchone()[0], 1)
                reader.commit()
                self.assertEqual(reader.execute('SELECT value FROM sample').fetchone()[0], 2)
            finally:
                reader.close()
                writer.close()
