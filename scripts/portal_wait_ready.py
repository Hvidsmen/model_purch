"""Wait for a local portal HTTP response after Task Scheduler starts it."""
import argparse
from pathlib import Path
import time
from urllib.error import URLError
from urllib.request import build_opener, ProxyHandler


def wait_ready(address, port, log, timeout=1800):
    host = {'0.0.0.0': '127.0.0.1', '::': '::1'}.get(address, address)
    if ':' in host:
        host = f'[{host}]'
    url = f'http://{host}:{port}/accounts/login/'
    opener = build_opener(ProxyHandler({}))
    deadline = time.monotonic() + timeout
    next_notice = 0
    print('Waiting for portal HTTP response; preparation can take several minutes.', flush=True)
    while time.monotonic() < deadline:
        try:
            with opener.open(url, timeout=3) as response:
                if response.status == 200:
                    print('PORTAL READY. You can refresh the browser now.', flush=True)
                    return 0
        except (URLError, OSError):
            pass
        now = time.monotonic()
        if now >= next_notice:
            print('Not ready yet. Latest log:', flush=True)
            try:
                # Read only the end of the potentially large append-only log.
                with Path(log).open('rb') as stream:
                    stream.seek(0, 2)
                    stream.seek(max(0, stream.tell() - 4096))
                    lines = stream.read().decode('utf-8', errors='replace').splitlines()
                print('\n'.join(lines[-3:]), flush=True)
            except OSError:
                print('Waiting for log file.', flush=True)
            next_notice = now + 15
        time.sleep(2)
    print('Readiness not confirmed within 30 minutes. Check portal-task.ps1 status. '
          'The background task was not stopped; do not start a second copy.', flush=True)
    return 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--address', required=True)
    parser.add_argument('--port', type=int, required=True)
    parser.add_argument('--log', required=True)
    args = parser.parse_args()
    return wait_ready(args.address, args.port, args.log)


if __name__ == '__main__':
    raise SystemExit(main())
