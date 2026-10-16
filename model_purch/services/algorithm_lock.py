"""OS-owned locks: released when the worker exits, including after a crash."""
from contextlib import contextmanager
from functools import wraps
from pathlib import Path
import os
from django.conf import settings
from django.http import JsonResponse


class AlgorithmBusy(Exception):
    pass


@contextmanager
def algorithm_lock(run_id):
    root = Path(getattr(settings, 'ALGORITHM_LOCK_DIR', Path(settings.BASE_DIR) / '.local' / 'algorithm-locks'))
    root.mkdir(parents=True, exist_ok=True)
    # Never unlink lock files: another process may already hold the same inode.
    with (root / f'{int(run_id)}.lock').open('a+b') as stream:
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b'0')
            stream.flush()
        stream.seek(0)
        if os.name == 'nt':
            import msvcrt
            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as error:
                raise AlgorithmBusy from error
        else:
            import fcntl
            try:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise AlgorithmBusy from error
        try:
            yield
        finally:
            if os.name == 'nt':
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def locked_algorithm(view):
    @wraps(view)
    def wrapped(request, run_id, *args, **kwargs):
        try:
            with algorithm_lock(run_id):
                return view(request, run_id, *args, **kwargs)
        except AlgorithmBusy:
            return JsonResponse({'error': 'Шаг ещё выполняется. Дождитесь его окончания перед отменой или повторным запуском.'}, status=409)
    return wrapped
