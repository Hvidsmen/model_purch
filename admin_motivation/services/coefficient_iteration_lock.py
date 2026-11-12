"""Non-blocking process lock; OS releases it if the web worker terminates."""
from contextlib import contextmanager
from pathlib import Path
import os,tempfile
from django.core.exceptions import ValidationError


@contextmanager
def operation(iteration_id):
    root=Path(tempfile.gettempdir())/'portal-coefficient-iteration-locks'
    root.mkdir(mode=0o700,exist_ok=True)
    handle=(root/(str(int(iteration_id))+'.lock')).open('a+b')
    locked=False
    try:
        if os.name=='nt':
            import msvcrt
            handle.seek(0,2)
            if handle.tell()==0:handle.write(b'0');handle.flush()
            handle.seek(0)
            try:msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
            except OSError:raise ValidationError('Эта итерация уже рассчитывается. Дождитесь завершения.')
        else:
            import fcntl
            try:fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:raise ValidationError('Эта итерация уже рассчитывается. Дождитесь завершения.')
        locked=True;yield
    finally:
        if locked:
            if os.name=='nt':handle.seek(0);msvcrt.locking(handle.fileno(),msvcrt.LK_UNLCK,1)
            else:fcntl.flock(handle,fcntl.LOCK_UN)
        handle.close()
