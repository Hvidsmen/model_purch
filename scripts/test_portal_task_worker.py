import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, Mock

from scripts import portal_task_worker as worker


class ScheduledWorkerTests(unittest.TestCase):
    def test_preparation_failure_is_logged_and_returned(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            local = root / '.local'
            local.mkdir()
            config = {'Database': 'C:/Работа/db.sqlite3', 'BindAddress': '0.0.0.0', 'Port': 8000}
            (local / 'task-settings.json').write_text(json.dumps(config), encoding='utf-8-sig')
            with patch.object(worker, 'ROOT', root), patch.object(worker.subprocess, 'Popen', return_value=Mock(pid=123,wait=Mock(return_value=7))) as run, patch('scripts.portal_task_process.record',return_value=456), patch('scripts.portal_task_process.clear'):
                self.assertEqual(worker.main(), 7)
            arguments = run.call_args.args[0]
            self.assertIn(config['Database'], arguments)
            self.assertEqual(arguments[-2:], ['-Port', '8000'])
            self.assertEqual(run.call_args.kwargs['stderr'], worker.subprocess.STDOUT)
            self.assertIn('exit code 7', (local / 'portal-task.log').read_text())

    def test_missing_configuration_produces_diagnostic_log(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(worker, 'ROOT', root), patch.object(worker.subprocess, 'run') as run:
                self.assertEqual(worker.main(), 1)
            run.assert_not_called()
            self.assertIn('FileNotFoundError', (root / '.local' / 'portal-task.log').read_text())
