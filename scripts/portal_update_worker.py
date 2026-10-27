"""Independent Task Scheduler job; status survives stopping the portal task."""
import json
import os
from pathlib import Path
import queue
import re
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit
from urllib.request import build_opener, ProxyHandler

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from scripts import portal_update_state as protocol

BASE_URL = '/accounts/updates/'


class UpdateFailure(Exception):
    pass


class Updater:
    def __init__(self, root, run_id):
        self.root, self.run_id = Path(root), run_id
        self.config = protocol.configuration(root)
        self.task = protocol.read(self.root / '.local' / 'task-settings.json')
        if not self.task or not Path(self.task['Database']).is_file():
            raise UpdateFailure('Не найдена настройка действующей базы портала.')
        self.folder = protocol.run_directory(root, run_id)
        self.retry = threading.Event()
        self.server = None
        # Keep maintenance assets in memory: checkout is replaced during the run.
        self.html = (self.root / 'scripts' / 'portal_update_maintenance.html').read_bytes().replace(b'data-maintenance="true"', f'data-maintenance="true" data-run="{run_id}"'.encode())
        self.javascript = (self.root / 'base' / 'static' / 'base' / 'portal_updates.js').read_bytes()

    def report(self, stage, detail=''):
        protocol.progress(self.root, self.run_id, stage, detail)

    def output(self, line):
        # The browser receives only known messages, never raw command output.
        if line.startswith('Backup copy:'):
            match = re.search(r'(\d+(?:\.\d+)?)%', line)
            if match:
                protocol.update(self.root, self.run_id, detail=f'Копирование резервной копии: {match[1]}%')
        elif line.startswith('Backup integrity check') or line.startswith('Backup copy complete.'):
            protocol.update(self.root, self.run_id, detail='Проверка целостности резервной копии')
        elif line.startswith('Backup waiting'):
            protocol.update(self.root, self.run_id, detail='Резервная копия ожидает завершения записи в SQLite')
        elif line.startswith('Backup verified'):
            protocol.update(self.root, self.run_id, detail='Резервная копия проверена. Применение миграций')
        elif line.startswith('Обновление статических файлов…'):
            self.report('static')
        elif line.startswith('Проверка и применение миграций…'):
            protocol.update(self.root, self.run_id, detail='Проверка миграций. При изменении базы создаётся резервная копия')

    def command(self, arguments, timeout=300, capture=False):
        environment = dict(os.environ, PYTHONIOENCODING='utf-8', PYTHONUNBUFFERED='1')
        with (self.folder / 'worker.log').open('a', encoding='utf-8') as logfile:
            process = subprocess.Popen(arguments, cwd=self.root, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace', env=environment)
            lines = queue.Queue()
            stop_reading = threading.Event()
            def drain():
                # A descendant may inherit stdout after its parent exits. Only
                # this thread closes the stream: close() in the main thread can
                # wait forever for the reader's buffered-I/O lock.
                try:
                    for line in process.stdout:
                        if not stop_reading.is_set():lines.put(line)
                finally:
                    process.stdout.close()
                    lines.put(None)
            reader = threading.Thread(target=drain, daemon=True)
            reader.start()
            deadline, heartbeat = time.monotonic() + timeout, 0
            captured = []
            def consume(line):
                if line:
                    logfile.write(line)
                    logfile.flush()
                    self.output(line.strip())
                    if capture:
                        captured.append(line)
            try:
                while True:
                    result = process.poll()
                    if result is not None:
                        # Normally EOF follows immediately. Never require it:
                        # a surviving descendant can keep the pipe open.
                        reader.join(timeout=.5)
                        drain_deadline=time.monotonic()+.5
                        while time.monotonic()<drain_deadline:
                            try:consume(lines.get_nowait())
                            except queue.Empty:break
                        if result:
                            raise UpdateFailure('Этап завершился с ошибкой. Подробности сохранены в журнале обновления.')
                        return ''.join(captured).strip()
                    if time.monotonic() > deadline:
                        raise UpdateFailure('Превышено время выполнения этапа. Подробности в журнале обновления.')
                    try:
                        consume(lines.get(timeout=.5))
                    except queue.Empty:
                        pass
                    if time.monotonic() >= heartbeat:
                        protocol.update(self.root, self.run_id)
                        heartbeat = time.monotonic() + 5
            finally:
                stop_reading.set()
                if process.poll() is None:
                    if os.name == 'nt':
                        try:
                            subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], capture_output=True, timeout=30)
                        except (OSError, subprocess.TimeoutExpired):
                            process.kill()
                    else:
                        process.kill()
                    try:process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                # Do not block on stdout.close() while a descendant owns the pipe.
                reader.join(timeout=.5)

    def task_command(self, action):
        return self.command(['powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
            '-File', str(self.root / 'scripts' / 'portal_update_task.ps1'), '-Action', action,
            '-TaskName', self.config['task_name'], '-PortalTaskName', self.config['portal_task_name']], timeout=60)

    def maintenance(self, timeout=20):
        if self.server is not None:
            return
        updater = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass  # Never log bearer tokens or request details.

            def send(self, status, body, kind='application/json; charset=utf-8'):
                if not isinstance(body, bytes):
                    body = json.dumps(body, ensure_ascii=False).encode('utf-8')
                self.send_response(status)
                self.send_header('Content-Type', kind)
                self.send_header('Content-Length', str(len(body)))
                self.send_header('Cache-Control', 'no-store')
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.send_header('X-Frame-Options', 'DENY')
                self.send_header('Content-Security-Policy', "default-src 'none'; script-src 'self'; style-src 'unsafe-inline'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'")
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                path = urlsplit(self.path).path
                if path == BASE_URL + 'maintenance.js':
                    return self.send(200, updater.javascript, 'text/javascript; charset=utf-8')
                if path == BASE_URL + f'status/{updater.run_id}/':
                    if not protocol.authorized(updater.root, updater.run_id, self.headers.get('X-Portal-Update-Status', '')):
                        return self.send(403, {'error': 'Для просмотра этапов откройте вкладку, из которой запущено обновление.'})
                    return self.send(200, protocol.state(updater.root, updater.run_id))
                if path.startswith(BASE_URL + 'health'):
                    return self.send(503, {'ready': False})
                return self.send(503, updater.html, 'text/html; charset=utf-8')

            def do_POST(self):
                path = urlsplit(self.path).path
                origin = urlsplit(self.headers.get('Origin', ''))
                same_origin = origin.scheme in ('http', 'https') and origin.netloc == self.headers.get('Host')
                if path != BASE_URL + f'retry/{updater.run_id}/' or not same_origin or not protocol.authorized(updater.root, updater.run_id, self.headers.get('X-Portal-Update-Control', ''), control=True):
                    return self.send(403, {'error': 'Недостаточно прав для повторного запуска.'})
                current = protocol.state(updater.root, updater.run_id)
                if current['status'] != 'failed' or not current.get('maintenance'):
                    return self.send(409, {'error': 'Повторный запуск сейчас недоступен.'})
                protocol.update(updater.root, updater.run_id, retry_latest=True)
                updater.retry.set()
                return self.send(202, {'accepted': True})

        class Server(ThreadingHTTPServer):
            daemon_threads = True
            # Windows SO_REUSEADDR can attach to an already occupied port.
            allow_reuse_address = os.name != 'nt'
        address = self.task['BindAddress']
        if ':' in address:
            Server.address_family = socket.AF_INET6
        deadline=time.monotonic()+timeout
        while True:
            try:
                self.server=Server((address,int(self.task['Port'])),Handler)
                break
            except OSError as error:
                import errno
                occupied=error.errno==errno.EADDRINUSE or getattr(error,'winerror',None)==10048
                if not occupied:
                    raise UpdateFailure('Не удалось открыть экран обслуживания. Проверьте адрес и права на порт; подробности в журнале.') from error
                if time.monotonic()>=deadline:
                    raise UpdateFailure(f"Порт {self.task['Port']} остался занят после остановки задания. Код и база ещё не обновлялись. Проверьте оставшийся процесс портала; чужие процессы автоматически не останавливаются.") from error
                protocol.update(self.root,self.run_id,detail='Задание остановлено. Ожидаем освобождения порта портала')
                time.sleep(.25)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        protocol.update(self.root, self.run_id, maintenance=True)

    def close_maintenance(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.server = None

    def verify(self, target, timeout=120):
        host = {'0.0.0.0': '127.0.0.1', '::': '::1'}.get(self.task['BindAddress'], self.task['BindAddress'])
        if ':' in host:
            host = f'[{host}]'
        url = f'http://{host}:{self.task["Port"]}{BASE_URL}health/'
        opener = build_opener(ProxyHandler({}))
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                with opener.open(url, timeout=3) as response:
                    data = json.load(response)
                    if data.get('ready') is True and data.get('revision') == target:
                        return
            except (OSError, ValueError):
                pass
            protocol.update(self.root, self.run_id)
            time.sleep(2)
        raise UpdateFailure('Новая версия не подтвердила готовность. Проверьте журнал запуска или повторите подготовку.')

    def attempt(self):
        current = protocol.state(self.root, self.run_id)
        protocol.update(self.root, self.run_id, status='running', error='', finished_at=None, retry_latest=False, attempt=current['attempt'] + 1)
        self.report('fetch')
        self.task_command('check')
        branch = self.command(['git', 'branch', '--show-current'], capture=True)
        if branch != 'main':
            raise UpdateFailure('Обновление доступно только для ветки main. Портал не остановлен.')
        if self.command(['git', 'status', '--porcelain'], capture=True):
            raise UpdateFailure('В папке портала есть локальные изменения. Они сохранены; обновление не выполнено.')
        old = self.command(['git', 'rev-parse', 'HEAD'], capture=True)
        target = None if current.get('retry_latest') else current.get('target_revision')
        if not target:
            self.command(['git', '-c', 'gc.auto=0', '-c', 'maintenance.auto=false', 'fetch', 'origin', 'main'], timeout=180)
            target = self.command(['git', 'rev-parse', 'FETCH_HEAD'], capture=True)
        if not re.fullmatch(r'[0-9a-f]{40,64}', target):
            raise UpdateFailure('Не удалось определить новую версию.')
        self.command(['git', 'merge-base', '--is-ancestor', old, target])
        protocol.update(self.root, self.run_id, from_revision=current.get('from_revision') or old, target_revision=target)
        if old == target and not current.get('portal_stopped'):
            self.report('complete', 'Установлена актуальная версия. Перезапуск не потребовался')
            protocol.update(self.root, self.run_id, status='completed', finished_at=protocol.utcnow(), maintenance=False)
            return
        self.report('stop')
        self.task_command('stop-portal')
        protocol.update(self.root, self.run_id, portal_stopped=True)
        self.maintenance()
        self.report('checkout')
        self.command(['git', '-c', 'gc.auto=0', '-c', 'maintenance.auto=false', 'merge', '--ff-only', target], timeout=180)
        self.report('dependencies')
        self.command([sys.executable, '-m', 'pip', 'install', '-r', 'requirements.txt', '--disable-pip-version-check', '--no-input'], timeout=1800)
        self.report('prepare')
        self.command([sys.executable, '-u', 'scripts/prepare_local.py', '--database', self.task['Database']], timeout=7200)
        # prepare_local performs backups/migrations/collectstatic and persists DB selection.
        protocol.write(self.folder / 'prepared.json', {'database': self.task['Database'], 'revision': target, 'consumed': False})
        self.report('starting')
        self.close_maintenance()
        self.task_command('start-portal')
        self.report('verify')
        self.verify(target)
        self.report('complete')
        protocol.update(self.root, self.run_id, status='completed', maintenance=False, finished_at=protocol.utcnow())

    def run(self):
        try:
            while True:
                try:
                    self.attempt()
                    return 0
                except Exception as error:
                    with (self.folder / 'worker.log').open('a', encoding='utf-8') as log:
                        import traceback
                        traceback.print_exc(file=log)
                    current = protocol.state(self.root, self.run_id)
                    if current.get('portal_stopped'):
                        # A failed server may still own the port. Stop only our verified task.
                        try:
                            self.task_command('stop-portal')
                            self.maintenance()
                        except Exception:
                            pass
                    message = str(error) if isinstance(error, UpdateFailure) else 'Обновление не завершено. Подробности в журнале запуска.'
                    protocol.update(self.root, self.run_id, status='failed', error=message, detail=message,
                        maintenance=self.server is not None, finished_at=protocol.utcnow())
                    if self.server is None:
                        return 1
                    # A separate live task keeps status and an authenticated retry on the same port.
                    self.retry.wait()
                    self.retry.clear()
        finally:
            self.close_maintenance()


def main():
    if os.name != 'nt':
        raise SystemExit('Browser updates require the configured Windows Task Scheduler deployment.')
    with protocol.lock(protocol.directory(ROOT) / 'worker.lock'):
        current = protocol.active(ROOT)
        if not current or current['status'] == 'completed' or current['status'] == 'failed' and not current.get('maintenance'):
            return 0
        request = protocol.read(protocol.run_directory(ROOT, current['id']) / 'request.json', {})
        if not request.get('armed'):
            return 0  # Credentials must reach the browser before any task can stop Django.
        try:
            return Updater(ROOT, current['id']).run()
        except Exception:
            protocol.update(ROOT, current['id'], status='failed', error='Не удалось запустить механизм обновления. Проверьте настройки задания Windows.', finished_at=protocol.utcnow())
            return 1


if __name__ == '__main__':
    raise SystemExit(main())
