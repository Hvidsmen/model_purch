"""Foreground Task Scheduler entry point; capture preparation and server output."""
import json
import os
from pathlib import Path
import subprocess
import sys
from datetime import datetime

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main():
    local = ROOT / '.local'
    local.mkdir(exist_ok=True)
    logfile = local / 'portal-task.log'
    with logfile.open('ab', buffering=0) as output:
        def report(message):
            output.write(f'{datetime.now().isoformat(timespec="seconds")} {message}\n'.encode('utf-8'))
        try:
            report('Task started; loading configuration.')
            config = json.loads((local / 'task-settings.json').read_text(encoding='utf-8-sig'))
            arguments = ['powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
                         '-File', str(ROOT / 'start.ps1'), '-Database', config['Database'],
                         '-BindAddress', config['BindAddress'], '-Port', str(config['Port'])]
            # Only the separate updater can prepare the checkout while its status
            # server occupies the portal port. start.ps1 consumes a one-use marker.
            from scripts.portal_update_state import active, run_directory, read
            current = active(ROOT)
            if current and current.get('stage') in ('starting', 'verify') and read(run_directory(ROOT, current['id']) / 'prepared.json'):
                arguments += ['-PreparedUpdate', current['id']]
            # No password in arguments or settings; use the scheduled Windows identity.
            environment = dict(os.environ, PYTHONIOENCODING='utf-8')
            from scripts.portal_task_process import record,clear
            process=subprocess.Popen(arguments,cwd=ROOT,stdin=subprocess.DEVNULL,
                                     stdout=output,stderr=subprocess.STDOUT,env=environment)
            stamp=None
            try:
                stamp=record(ROOT,process)
                result=process.wait()
            except Exception:
                if process.poll() is None:
                    # Identity could not be stored: do not leave an untracked server.
                    try:subprocess.run(['taskkill.exe','/PID',str(process.pid),'/T','/F'],check=True,timeout=20,capture_output=True)
                    except (OSError,subprocess.SubprocessError):process.kill()
                raise
            finally:
                if stamp is not None:clear(ROOT,process.pid,stamp)
            report(f'Task ended; exit code {result}.')
            return result
        except Exception as error:
            report(f'Start failed: {type(error).__name__}: {error}')
            return 1


if __name__ == '__main__':
    sys.exit(main())
