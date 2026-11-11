"""Per-request diagnostics outside the financial transaction, shared by web workers."""
import json
import os
from pathlib import Path
import tempfile
import time
import uuid
from django.core import signing
from django.core.exceptions import PermissionDenied, ValidationError

SALT='director-scenario-progress-v1'


def token_for(user):
    return signing.dumps({'owner':user.pk,'nonce':uuid.uuid4().hex},salt=SALT)


class Progress:
    def __init__(self,token,user):
        try:data=signing.loads(token,salt=SALT,max_age=86400)
        except signing.BadSignature:raise ValidationError('Ссылка статуса устарела. Обновите страницу.')
        if data.get('owner')!=user.pk:raise PermissionDenied
        nonce=data.get('nonce','')
        if len(nonce)!=32 or any(c not in '0123456789abcdef' for c in nonce):raise ValidationError('Неверный ключ статуса.')
        root=Path(tempfile.gettempdir())/'portal-director-progress'
        root.mkdir(mode=0o700,exist_ok=True)
        self.path=root/(nonce+'.json')

    def read(self):
        try:data=json.loads(self.path.read_text())
        except FileNotFoundError:return {'state':'pending','stage':'Ожидание запуска'}
        now=time.time()
        return {**data,'elapsed':max(0,int(data.get('finished_at',now)-data['started_at'])),
            'stage_elapsed':max(0,int(data.get('finished_at',now)-data['stage_at']))}

    def begin(self):
        now=time.time()
        data={'state':'running','stage':'Проверка параметров','started_at':now,'stage_at':now,'step':0}
        try:
            with self.path.open('x') as target:json.dump(data,target)
        except FileExistsError:raise ValidationError('Этот запуск уже принят. Обновите страницу перед новым запуском.')
        # Diagnostic files expire after a day; never remove a current run.
        for path in list(self.path.parent.glob('*.json'))[:200]:
            try:
                if path.stat().st_mtime<now-86400:path.unlink(missing_ok=True)
            except FileNotFoundError:pass

    def update(self,stage,step=None,state='running',url=None):
        data=self.read()
        if data['state']=='pending':return
        data.update(stage=stage,stage_at=time.time(),state=state)
        if step is not None:data['step']=step
        if url:data['url']=url
        if state!='running':data['finished_at']=time.time()
        data.pop('elapsed',None);data.pop('stage_elapsed',None)
        handle,path=tempfile.mkstemp(dir=self.path.parent,prefix=self.path.stem,suffix='.tmp')
        try:
            with os.fdopen(handle,'w') as target:json.dump(data,target)
            os.replace(path,self.path)
        finally:
            Path(path).unlink(missing_ok=True)
