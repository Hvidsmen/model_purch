import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock,patch
from scripts import portal_task_process as process
from scripts import portal_update_state as protocol


class TaskProcessTests(unittest.TestCase):
    def test_recorded_child_tree_is_stopped_before_wrapper(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            with patch.object(process,'process_stamp',return_value=456):
                self.assertEqual(process.record(root,Mock(pid=123)),456)
            with patch.object(process,'process_stamp',side_effect=[456,None]),patch.object(process.subprocess,'run') as run:
                process.stop(root)
            run.assert_called_once_with(['taskkill.exe','/PID','123','/T','/F'],check=True,timeout=20,capture_output=True)
            self.assertFalse((root/'.local'/'portal-task-process.json').exists())

    def test_reused_pid_and_foreign_root_never_killed(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            path=root/'.local'/'portal-task-process.json'
            protocol.write(path,{'root':str(root.resolve()),'pid':123,'stamp':456})
            with patch.object(process,'process_stamp',return_value=789),patch.object(process.subprocess,'run') as run:
                process.stop(root)
            run.assert_not_called()
            protocol.write(path,{'root':'other-checkout','pid':123,'stamp':456})
            with patch.object(process.subprocess,'run') as run:
                with self.assertRaises(RuntimeError):process.stop(root)
            run.assert_not_called()

    def test_late_cleanup_does_not_erase_new_process_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);path=root/'.local'/'portal-task-process.json'
            protocol.write(path,{'root':str(root.resolve()),'pid':123,'stamp':789})
            process.clear(root,123,456)
            self.assertTrue(path.exists())
            process.clear(root,123,789)
            self.assertFalse(path.exists())
