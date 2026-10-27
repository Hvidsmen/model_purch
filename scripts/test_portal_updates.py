import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from scripts import portal_update_state as state
from scripts.portal_update_worker import Updater, UpdateFailure

ROOT = Path(__file__).resolve().parent.parent


class UpdateProtocolTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.local = self.root / '.local'
        self.local.mkdir()
        self.database = self.root / 'db.sqlite3'
        self.database.touch()
        state.write(self.local / 'browser-updates.json', {'root': str(self.root), 'task_name': 'Portal-Updater', 'portal_task_name': 'Portal'})
        state.write(self.local / 'task-settings.json', {'Database': str(self.database), 'BindAddress': '127.0.0.1', 'Port': 0})
        (self.root / 'scripts').mkdir()
        (self.root / 'scripts' / 'portal_update_maintenance.html').write_bytes((ROOT / 'scripts' / 'portal_update_maintenance.html').read_bytes())
        (self.root / 'base' / 'static' / 'base').mkdir(parents=True)
        (self.root / 'base' / 'static' / 'base' / 'portal_updates.js').write_bytes((ROOT / 'base' / 'static' / 'base' / 'portal_updates.js').read_bytes())
        self.record, self.token, self.control = state.create(self.root, 1, 'Administrator')

    def test_duplicate_dispatch_and_token_separation(self):
        with self.assertRaises(ValueError):
            state.create(self.root, 2, 'Another administrator')
        self.assertTrue(state.authorized(self.root, self.record['id'], self.token))
        self.assertFalse(state.authorized(self.root, self.record['id'], self.token, control=True))
        self.assertTrue(state.authorized(self.root, self.record['id'], self.control, control=True))
        self.assertNotIn(self.token, json.dumps(state.history(self.root)))
        with self.assertRaises(ValueError):
            state.state(self.root, '../outside')
        with state.lock(self.local / 'test.lock'):
            with self.assertRaises(OSError):
                with state.lock(self.local / 'test.lock'):
                    self.fail('Lock incorrectly acquired twice')
        state.update(self.root, self.record['id'], status='completed')
        next_run, _, _ = state.create(self.root, 2, 'Another administrator')
        self.assertNotEqual(next_run['id'], self.record['id'])
        self.assertEqual(len(state.history(self.root)), 2)

    def test_preparation_marker_is_once_and_bound_to_revision_and_database(self):
        folder = state.run_directory(self.root, self.record['id'])
        state.write(self.local / 'config.json', {'database_path': str(self.database)})
        state.write(folder / 'prepared.json', {'database': str(self.database), 'revision': 'a' * 40, 'consumed': False})
        state.update(self.root, self.record['id'], status='running', stage='verify')
        with patch.object(state, 'revision', return_value='b' * 40):
            self.assertFalse(state.prepared(self.root, self.record['id'], self.database))
        with patch.object(state, 'revision', return_value='a' * 40):
            self.assertFalse(state.prepared(self.root, self.record['id'], self.root / 'other.sqlite3'))
            self.assertTrue(state.prepared(self.root, self.record['id'], self.database))
            self.assertFalse(state.prepared(self.root, self.record['id'], self.database))

    def updater(self):
        return Updater(self.root, self.record['id'])

    def test_same_port_status_server_enforces_separate_read_and_control_tokens(self):
        worker = self.updater()
        worker.maintenance()
        self.addCleanup(worker.close_maintenance)
        address = f'http://127.0.0.1:{worker.server.server_address[1]}'
        endpoint = address + f'/accounts/updates/status/{self.record["id"]}/'
        with self.assertRaises(HTTPError) as denied:
            urlopen(endpoint)
        self.assertEqual(denied.exception.code, 403)
        with urlopen(Request(endpoint, headers={'X-Portal-Update-Status': self.token})) as response:
            result = json.load(response)
            self.assertEqual(result['id'], self.record['id'])
            self.assertNotIn('status_hash', result)
        with self.assertRaises(HTTPError) as down:
            urlopen(address + '/accounts/updates/health/')
        self.assertEqual(down.exception.code, 503)
        state.update(self.root, self.record['id'], status='failed', maintenance=True)
        retry = address + f'/accounts/updates/retry/{self.record["id"]}/'
        for token, origin in [(self.token, address), (self.control, 'https://wrong.example')]:
            with self.assertRaises(HTTPError) as denied:
                urlopen(Request(retry, data=b'{}', headers={'X-Portal-Update-Control': token, 'Origin': origin}))
            self.assertEqual(denied.exception.code, 403)
        with urlopen(Request(retry, data=b'{}', headers={'X-Portal-Update-Control': self.control, 'Origin': address})) as accepted:
            self.assertEqual(accepted.status, 202)
        self.assertTrue(worker.retry.is_set())
        worker.close_maintenance()
        # Port is released for Django during the handoff.
        listener = socket.socket()
        self.addCleanup(listener.close)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(('127.0.0.1', int(address.rsplit(':', 1)[1])))

    def test_clean_fast_forward_pipeline_prepares_before_starting_and_verifies_revision(self):
        worker = self.updater()
        old, target = 'a' * 40, 'b' * 40
        commands, tasks, timeline = [], [], []
        def command(args, **kwargs):
            commands.append(args)
            timeline.append(args)
            if '--report' in args:
                state.write(Path(args[args.index('--report') + 1]), {'install': [{'metadata': {'name': 'Example', 'version': '1.0'}}]})
            if args[:3] == ['git', 'branch', '--show-current']: return 'main'
            if args[:3] == ['git', 'status', '--porcelain']: return ''
            if args[:3] == ['git', 'rev-parse', 'HEAD']: return old
            if args[:3] == ['git', 'rev-parse', 'FETCH_HEAD']: return target
            return ''
        def task(action):
            tasks.append(action)
            timeline.append(action)
            if action == 'start-portal':
                marker = state.read(worker.folder / 'prepared.json')
                self.assertEqual(marker['revision'], target)
                self.assertIsNone(worker.server)
        with patch.object(worker, 'command', side_effect=command), patch.object(worker, 'task_command', side_effect=task), patch.object(worker, 'verify') as verify:
            self.assertEqual(worker.run(), 0)
        self.assertEqual(tasks, ['check', 'stop-portal', 'start-portal'])
        dry_run = next(args for args in commands if '--dry-run' in args)
        wheel = next(args for args in commands if 'wheel' in args)
        install = next(args for args in commands if 'install' in args and '--dry-run' not in args)
        self.assertLess(timeline.index(dry_run), timeline.index('stop-portal'))
        self.assertLess(timeline.index(wheel), timeline.index('stop-portal'))
        self.assertGreater(timeline.index(install), timeline.index('stop-portal'))
        self.assertIn('--no-index', install)
        self.assertFalse((worker.folder / 'wheels').exists())
        verify.assert_called_once_with(target)
        self.assertTrue(any('scripts/prepare_local.py' in args for args in commands))
        self.assertEqual(state.state(self.root, self.record['id'])['status'], 'completed')

    def test_dependency_failure_does_not_stop_portal_or_change_checkout(self):
        worker = self.updater()
        commands = []
        def command(args, **kwargs):
            commands.append(args)
            if '--dry-run' in args:
                raise UpdateFailure('Dependency download unavailable')
            if args[:3] == ['git', 'branch', '--show-current']: return 'main'
            if args[:3] == ['git', 'rev-parse', 'HEAD']: return 'a' * 40
            if args[:3] == ['git', 'rev-parse', 'FETCH_HEAD']: return 'b' * 40
            return ''
        with patch.object(worker, 'command', side_effect=command), patch.object(worker, 'task_command') as task:
            self.assertEqual(worker.run(), 1)
        task.assert_called_once_with('check')
        self.assertFalse(any('--ff-only' in args for args in commands))
        self.assertFalse(state.state(self.root, self.record['id'])['maintenance'])

    def test_dirty_checkout_aborts_without_stopping_portal(self):
        worker = self.updater()
        def command(args, **kwargs):
            return 'main' if args[1] == 'branch' else ' M file.py'
        with patch.object(worker, 'command', side_effect=command), patch.object(worker, 'task_command') as task:
            self.assertEqual(worker.run(), 1)
        task.assert_called_once_with('check')
        self.assertFalse(state.state(self.root, self.record['id'])['maintenance'])
        self.assertIn('локальные изменения', state.state(self.root, self.record['id'])['error'])

    def test_backup_progress_is_structured_and_other_output_is_not_exposed(self):
        worker = self.updater()
        worker.output('secret=connection-password')
        self.assertNotIn('secret', json.dumps(state.state(self.root, self.record['id'])))
        worker.output('Backup copy: 35.4% (354/1000 pages); elapsed 5s')
        self.assertEqual(state.state(self.root, self.record['id'])['detail'], 'Копирование резервной копии: 35.4%')
        worker.output('Backup integrity check running; elapsed 20s')
        self.assertIn('Проверка целостности', state.state(self.root, self.record['id'])['detail'])

    def test_command_streams_progress_and_hides_failure_details(self):
        import sys
        worker = self.updater()
        worker.command([sys.executable, '-c', "print('Backup copy: 22.0% (22/100 pages); elapsed 1s')"])
        self.assertIn('22.0%', state.state(self.root, self.record['id'])['detail'])
        with self.assertRaises(UpdateFailure):
            worker.command([sys.executable, '-c', "print('private-password'); raise SystemExit(7)"])
        self.assertNotIn('private-password', json.dumps(state.state(self.root, self.record['id'])))
        self.assertIn('private-password', (worker.folder / 'worker.log').read_text())

    def test_exited_parent_does_not_wait_for_descendant_stdout(self):
        import os,signal,sys,time
        worker=self.updater()
        pidfile=self.root/'test-child.pid'
        code=("import subprocess,sys,pathlib; "
              "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(10)']); "
              "pathlib.Path(sys.argv[1]).write_text(str(child.pid)); "
              "print('parent completed',flush=True)")
        started=time.monotonic()
        try:
            output=worker.command([sys.executable,'-c',code,str(pidfile)],timeout=2,capture=True)
            self.assertEqual(output,'parent completed')
            self.assertLess(time.monotonic()-started,4)
        finally:
            if pidfile.exists():
                try:os.kill(int(pidfile.read_text()),signal.SIGTERM)
                except ProcessLookupError:pass

    def test_timeout_is_bounded_even_when_stdout_is_closed(self):
        import sys,time
        worker=self.updater();started=time.monotonic()
        with self.assertRaisesRegex(UpdateFailure,'Превышено время'):
            worker.command([sys.executable,'-c','import os,time; os.close(1); os.close(2); time.sleep(10)'],timeout=.2)
        self.assertLess(time.monotonic()-started,3)

    def test_busy_port_reports_clear_failure_after_bounded_wait(self):
        worker=self.updater()
        with socket.socket() as listener:
            listener.bind(('127.0.0.1',0));listener.listen()
            worker.task['Port']=listener.getsockname()[1]
            with self.assertRaisesRegex(UpdateFailure,'остался занят'):
                worker.maintenance(timeout=.01)
        self.assertIsNone(worker.server)

    def test_current_version_does_not_stop_or_reinstall(self):
        worker = self.updater()
        target = 'a' * 40
        def command(args, **kwargs):
            if args[1] == 'branch': return 'main'
            if args[1] == 'status': return ''
            if args[1] == 'rev-parse': return target
            return ''
        with patch.object(worker, 'command', side_effect=command), patch.object(worker, 'task_command') as task:
            self.assertEqual(worker.run(), 0)
        task.assert_called_once_with('check')
        self.assertEqual(state.state(self.root, self.record['id'])['status'], 'completed')
        self.assertIn('не потребовался', state.state(self.root, self.record['id'])['detail'])

    def test_active_worker_cannot_be_replaced_even_if_status_write_reported_failure(self):
        state.update(self.root, self.record['id'], status='failed', maintenance=False)
        with state.lock(self.local / 'portal-updates' / 'worker.lock'):
            with self.assertRaises(ValueError):
                state.create(self.root, 2, 'Another administrator')
        self.assertEqual(state.active(self.root)['id'], self.record['id'])

    def test_interrupted_worker_is_detected_without_mutating_status_on_get(self):
        state.update(self.root, self.record['id'], status='running', maintenance=True)
        path = state.run_directory(self.root, self.record['id']) / 'status.json'
        record = state.state(self.root, self.record['id'])
        record['updated_at'] = '2020-01-01T00:00:00+00:00'
        state.write(path, record)
        observed = state.observe(self.root, record)
        self.assertEqual(observed['status'], 'failed')
        self.assertEqual(state.state(self.root, self.record['id'])['status'], 'running')
        replacement, _, _ = state.create(self.root, 2, 'Administrator')
        self.assertNotEqual(replacement['id'], self.record['id'])
        self.assertEqual(state.state(self.root, self.record['id'])['status'], 'failed')
