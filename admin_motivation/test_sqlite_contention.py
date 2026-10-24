"""Exercise actual contention on two connections to a file-backed SQLite DB."""
import sqlite3
import tempfile
import threading
from pathlib import Path

from django.conf import settings
from django.db.backends.sqlite3.base import DatabaseWrapper
from django.test import SimpleTestCase


class SQLiteWriterTests(SimpleTestCase):
    def test_atomic_waits_for_writer_before_reading(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / 'contention.sqlite3')
            with sqlite3.connect(path) as seed:
                seed.execute('CREATE TABLE counters (value INTEGER)')
                seed.execute('INSERT INTO counters VALUES (0)')
            locked = threading.Event()
            release = threading.Event()
            failures = []

            def other_writer():
                try:
                    with sqlite3.connect(path) as db:
                        db.execute('BEGIN IMMEDIATE')
                        db.execute('UPDATE counters SET value = 1')
                        locked.set()
                        if not release.wait(5):
                            raise RuntimeError('test writer not released')
                except Exception as error:
                    failures.append(error)
                    locked.set()

            worker = threading.Thread(target=other_writer)
            worker.start()
            config = {**settings.DATABASES['default'], 'NAME': path}
            config['OPTIONS'] = {**config['OPTIONS'], 'timeout': 2}
            db = DatabaseWrapper(config, alias='contention')
            timer = threading.Timer(.15, release.set)
            try:
                self.assertTrue(locked.wait(5))
                self.assertFalse(failures)
                timer.start()
                db.ensure_connection()
                # Same transaction entry used by Django atomic(). A deferred BEGIN
                # would read 0 here, then fail immediately upgrading to a writer.
                db._start_transaction_under_autocommit()
                with db.cursor() as cursor:
                    cursor.execute('SELECT value FROM counters')
                    self.assertEqual(cursor.fetchone()[0], 1)
                    cursor.execute('UPDATE counters SET value = 2')
                db.connection.commit()
            finally:
                release.set()
                worker.join(5)
                timer.cancel()
                db.close()
            self.assertFalse(failures)
            with sqlite3.connect(path) as result:
                self.assertEqual(result.execute('SELECT value FROM counters').fetchone()[0], 2)
