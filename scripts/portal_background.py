"""Windows background lifecycle for the existing start.ps1 entry point."""
import argparse
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent.parent
LOCAL = ROOT / '.local'
STATE = LOCAL / 'background.json'
SETTINGS = LOCAL / 'background-settings.json'


def process_stamp(pid):
    """Identify a process by PID AND creation time; never terminate a reused PID."""
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        if ctypes.get_last_error() == 87:  # ERROR_INVALID_PARAMETER: process ended
            return None
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        exit_code = wintypes.DWORD()
        if not kernel.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            raise ctypes.WinError(ctypes.get_last_error())
        if exit_code.value != 259:  # STILL_ACTIVE
            return None
        times = [wintypes.FILETIME() for _ in range(4)]
        if not kernel.GetProcessTimes(handle, *(ctypes.byref(t) for t in times)):
            raise ctypes.WinError(ctypes.get_last_error())
        return (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
    finally:
        kernel.CloseHandle(handle)


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}


def save_json(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)


def running(state):
    return bool(state and state.get('stamp') is not None and process_stamp(state['pid']) == state['stamp'])


def stop():
    state = read_json(STATE)
    if running(state):
        subprocess.run(['taskkill.exe', '/PID', str(state['pid']), '/T', '/F'], check=True)
        deadline = time.monotonic() + 15
        while running(state) and time.monotonic() < deadline:
            time.sleep(.2)
        if running(state):
            raise RuntimeError('Не удалось остановить портал. Повторный сервер не запущен.')
    STATE.unlink(missing_ok=True)
    print('Портал остановлен.', flush=True)


def start(options):
    config = read_json(SETTINGS)
    config.update({key: value for key, value in {
        'database': options.database, 'bind': options.bind, 'port': options.port,
    }.items() if value is not None})
    config.setdefault('bind', '0.0.0.0')
    config.setdefault('port', 8000)
    if running(read_json(STATE)):
        raise RuntimeError('Портал уже запущен. Используйте restart.')
    # Refuse to overwrite another server, including an old foreground launch.
    with socket.socket() as probe:
        probe.bind((config['bind'], config['port']))
    save_json(SETTINGS, config)
    arguments = ['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass',
                 '-File', str(ROOT / 'start.ps1'), '-BindAddress', config['bind'],
                 '-Port', str(config['port'])]
    if config.get('database'):
        arguments += ['-Database', config['database']]
    stamp = time.strftime('%Y%m%d-%H%M%S')
    logfile = LOCAL / f'portal-{stamp}-{os.getpid()}.log'
    with logfile.open('ab', buffering=0) as output:
        process = subprocess.Popen(arguments, cwd=ROOT, stdin=subprocess.DEVNULL,
                                   stdout=output, stderr=subprocess.STDOUT,
                                   creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP)
    state = {'pid': process.pid, 'stamp': process_stamp(process.pid), 'log': str(logfile), **config}
    save_json(STATE, state)
    print(f'Фоновый процесс запущен: PID {process.pid}. Лог: {logfile}', flush=True)
    host = '127.0.0.1' if config['bind'] == '0.0.0.0' else config['bind']
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f'Ошибка подготовки или запуска. Посмотрите лог: {logfile}')
        try:
            with socket.create_connection((host, config['port']), timeout=.5):
                print(f'Портал принимает подключения: http://{host}:{config["port"]}/', flush=True)
                return
        except OSError:
            time.sleep(.5)
    print('Подготовка ещё выполняется. Проверьте status и лог; не запускайте второй экземпляр.', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['start', 'stop', 'restart', 'status'])
    parser.add_argument('--database')
    parser.add_argument('--bind')
    parser.add_argument('--port', type=int, choices=range(1, 65536), metavar='PORT')
    parser.add_argument('--update', action='store_true')
    options = parser.parse_args()
    if sys.platform != 'win32':
        parser.error('Этот запуск предназначен для Windows.')
    if options.update and options.action != 'restart':
        parser.error('--update используется только с restart.')
    LOCAL.mkdir(exist_ok=True)
    # A file lock also serializes separate PowerShell sessions.
    import msvcrt
    with (LOCAL / 'background.lock').open('a+b') as lock:
        lock.seek(0)
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        if options.action == 'status':
            state = read_json(STATE)
            print('Портал запущен.' if running(state) else 'Портал остановлен.')
            if state:
                print(f'PID: {state["pid"]}; адрес: {state["bind"]}:{state["port"]}; лог: {state["log"]}')
            return
        if options.update:
            if subprocess.check_output(['git', 'status', '--porcelain'], cwd=ROOT).strip():
                raise RuntimeError('Есть локальные изменения Git. Обновление отменено; портал не остановлен.')
            subprocess.run(['git', 'pull', '--ff-only'], cwd=ROOT, check=True)
        if options.action in ('stop', 'restart'):
            stop()
        if options.action in ('start', 'restart'):
            if options.update:
                subprocess.run([sys.executable, '-m', 'pip', 'install', '-r', str(ROOT / 'requirements.txt')], check=True)
            start(options)


if __name__ == '__main__':
    try:
        main()
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        print(f'Ошибка: {error}', file=sys.stderr)
        sys.exit(1)
