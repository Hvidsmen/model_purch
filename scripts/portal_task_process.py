"""Stop only the recorded portal child tree, before ending its Scheduler wrapper."""
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from scripts import portal_update_state as protocol
from scripts.portal_background import process_stamp


def record(root,process):
    stamp=process_stamp(process.pid)
    if stamp is None:
        raise RuntimeError('Portal child exited before its identity could be recorded.')
    protocol.write(Path(root)/'.local'/'portal-task-process.json',{'root':str(Path(root).resolve()),'pid':process.pid,'stamp':stamp})
    return stamp


def clear(root,pid,stamp):
    path=Path(root)/'.local'/'portal-task-process.json'
    current=protocol.read(path,{})
    if current.get('pid')==pid and current.get('stamp')==stamp:path.unlink(missing_ok=True)


def stop(root=ROOT):
    root=Path(root).resolve();path=root/'.local'/'portal-task-process.json'
    state=protocol.read(path,{})
    if not state:return
    if state.get('root')!=str(root) or type(state.get('pid')) is not int or state['pid']<=0 or type(state.get('stamp')) is not int:
        raise RuntimeError('Portal child identity is invalid; no process was stopped.')
    pid,stamp=state['pid'],state['stamp']
    if process_stamp(pid)==stamp:
        try:
            subprocess.run(['taskkill.exe','/PID',str(pid),'/T','/F'],check=True,timeout=20,capture_output=True)
        except subprocess.CalledProcessError:
            if process_stamp(pid)==stamp:raise
        deadline=time.monotonic()+10
        while process_stamp(pid)==stamp:
            if time.monotonic()>deadline:raise RuntimeError('Portal process tree did not stop.')
            time.sleep(.2)
    clear(root,pid,stamp)


if __name__=='__main__':
    if os.name!='nt' or sys.argv[1:]!=['stop']:
        raise SystemExit('Only the configured Windows portal stop operation is supported.')
    stop()
