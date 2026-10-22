import argparse
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import portal_background as portal


class BackgroundLifecycleTests(unittest.TestCase):
    def test_reused_pid_is_not_stopped(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / 'state.json'
            portal.save_json(state, {'pid': 123, 'stamp': 100})
            with patch.object(portal, 'STATE', state), patch.object(portal, 'process_stamp', return_value=200), patch.object(portal.subprocess, 'run') as run:
                portal.stop()
            run.assert_not_called()
            self.assertFalse(state.exists())

    def test_existing_process_prevents_duplicate_start(self):
        options = argparse.Namespace(database=None, bind=None, port=None)
        with patch.object(portal, 'read_json', return_value={'pid': 123, 'stamp': 100}), patch.object(portal, 'process_stamp', return_value=100), patch.object(portal.subprocess, 'Popen') as launch:
            with self.assertRaisesRegex(RuntimeError, 'уже запущен'):
                portal.start(options)
            launch.assert_not_called()

    def test_active_process_tree_is_stopped(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / 'state.json'
            portal.save_json(state, {'pid': 123, 'stamp': 100})
            with patch.object(portal, 'STATE', state), patch.object(portal, 'process_stamp', side_effect=[100, None, None]), patch.object(portal.subprocess, 'run') as run:
                portal.stop()
            run.assert_called_once_with(['taskkill.exe', '/PID', '123', '/T', '/F'], check=True)
            self.assertFalse(state.exists())

    def test_saved_settings_roundtrip(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'settings.json'
            config = {'database': 'C:/Данные/db.sqlite3', 'port': 8000}
            portal.save_json(path, config)
            self.assertEqual(portal.read_json(path), config)
