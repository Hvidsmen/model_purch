"""Isolated Git or private-package monitoring updates. Never runs git/pip/migrations on the portal."""
import argparse
from datetime import datetime,timezone
from hashlib import sha256
import http.client
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import time
import uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.split_monitoring_runtime import read_config,lock,ensure_running,stop,DEFAULT_CONFIG

REPOSITORY='https://github.com/bse-sb/Split-monitoring.git'
BRANCH='main'


class UpdateError(Exception):pass


def write(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix('.tmp');temp.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
    try:temp.chmod(0o600)
    except OSError:pass
    temp.replace(path)


def read(path,default=None):
    try:return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError,ValueError):return default


def directory(config_path):return Path(config_path).resolve().parent/'updates'


def status(config_path,run_id):
    if not re.fullmatch('[0-9a-f]{32}',str(run_id)):return None
    record=read(directory(config_path)/run_id/'status.json')
    if record and record.get('state') in ('queued','running') and time.time()-record.get('heartbeat',time.time())>900:
        return {**record,'state':'failed','message':'Процесс обновления перестал отвечать. Проверьте локальный worker.log и повторите запуск.'}
    return record


def start(config_path,actor,*,uploaded_file=None,expected_sha256=''):
    read_config(config_path)
    home=directory(config_path);home.mkdir(parents=True,exist_ok=True)
    with lock(home/'dispatch.lock'):
        current=read(home/'current.json')
        if current:
            existing=status(config_path,current.get('id'))
            if existing and existing['state'] in ('queued','running') and time.time()-existing['heartbeat']<900:return existing
        run_id=uuid.uuid4().hex;folder=home/run_id;folder.mkdir()
        record={'id':run_id,'actor':int(actor),'state':'queued','stage':'Ожидание запуска','started_at':datetime.now(timezone.utc).isoformat(),
                'heartbeat':time.time(),'steps':[],'repository':'bse-sb/Split-monitoring','branch':BRANCH,'message':''}
        if uploaded_file is not None:
            from scripts.split_monitoring_package import MAX_UPLOAD_BYTES,PackageError
            digest=sha256();size=0
            try:
                if expected_sha256 and not re.fullmatch('[0-9a-fA-F]{64}',expected_sha256):raise PackageError('SHA-256 должен содержать 64 шестнадцатеричных знака.')
                with (folder/'package.zip').open('wb') as target:
                    for chunk in uploaded_file.chunks():
                        size+=len(chunk)
                        if size>MAX_UPLOAD_BYTES:raise PackageError('Размер ZIP превышает 64 МБ.')
                        digest.update(chunk);target.write(chunk)
                if not size:raise PackageError('Загружен пустой ZIP.')
                if expected_sha256 and digest.hexdigest()!=expected_sha256.lower():raise PackageError('SHA-256 загруженного ZIP не совпал с указанным.')
                record.update(source='package',package_sha256=digest.hexdigest())
            except Exception:
                shutil.rmtree(folder);raise
        else:record['source']='git'
        write(folder/'status.json',record);write(home/'current.json',{'id':run_id})
        logfile=(folder/'worker.log').open('ab')
        options={'creationflags':subprocess.CREATE_NO_WINDOW} if os.name=='nt' else {'start_new_session':True}
        try:
            subprocess.Popen([sys.executable,str(Path(__file__).resolve()),'--config',str(Path(config_path).resolve()),'--run',run_id],
                cwd=home,stdin=subprocess.DEVNULL,stdout=logfile,stderr=logfile,**options)
        except OSError:
            record.update(state='failed',message='Не удалось запустить процесс обновления.');write(folder/'status.json',record);raise
        finally:logfile.close()
        return record


def frontend_signature(root):
    app=Path(root)/'app';values=[]
    if not app.is_dir():raise UpdateError('В репозитории нет каталога app.')
    paths=[p for p in app.rglob('*') if p.is_file() and 'dist' not in p.relative_to(app).parts and 'node_modules' not in p.parts
           and '.git' not in p.parts and p.relative_to(app).as_posix() not in ('src/data.json',) and p.suffix in ('.js','.jsx','.ts','.tsx','.mjs','.css','.html','.json')]
    for path in sorted(paths):
        data=path.read_bytes().replace(b'\r\n',b'\n')
        values.append((path.relative_to(app).as_posix(),sha256(data).hexdigest()))
    return sha256(json.dumps(values).encode()).hexdigest()


def requirement_signature(root):
    paths=sorted(Path(root).glob('requirements*.txt'))
    return sha256(b''.join(path.name.encode()+b'\0'+path.read_bytes().replace(b'\r\n',b'\n') for path in paths)).hexdigest()


def verify_build(app,previous,data_dir):
    dist=app/'app/dist'
    if not (dist/'data-app-build.json').is_file():
        if frontend_signature(app)!=frontend_signature(previous):
            raise UpdateError('В GitHub изменился интерфейс, но нет готовой app/dist. Нужна проверенная сборка этой версии; работающая версия сохранена.')
        shutil.copytree(previous/'app/dist',dist,dirs_exist_ok=True)
    manifest=read(dist/'data-app-build.json')
    if not manifest or manifest.get('kind')!='separate-data-v1':raise UpdateError('Нужна раздельная проверенная сборка separate-data-v1.')
    for key in ('html','snapshot'):
        item=manifest.get(key,{})
        path=(dist/item.get('path','')).resolve()
        if not path.is_relative_to(dist.resolve()):raise UpdateError('Некорректный путь сборки.')
        expected=item.get('sha256')
        if key=='snapshot' and not path.is_file():
            # Reviewed data never needs to be stored in Git. Hydrate only an
            # exact content-addressed snapshot already present on this server.
            candidates=[previous/'app/dist'/path.name,data_dir/'reviewed_snapshot.json',data_dir/'snapshot_revisions'/(str(expected)+'.reviewed.json')]
            match=next((p for p in candidates if p.is_file() and sha256(p.read_bytes()).hexdigest()==expected),None)
            if match:path.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(match,path)
        if not path.is_file() or sha256(path.read_bytes()).hexdigest()!=expected:
            raise UpdateError('Нет локального снимка либо не совпала контрольная сумма сборки. Работающая версия сохранена.')


class Updater:
    def __init__(self,config_path,run_id,*,repository=REPOSITORY):
        self.config_path=Path(config_path).resolve();self.home=self.config_path.parent
        self.folder=directory(config_path)/run_id;self.record=status(config_path,run_id)
        if self.record is None:raise UpdateError('Запуск не найден.')
        self.repository=repository;self.config=read_config(config_path)

    def stage(self,label):
        now=time.time()
        if self.record['steps'] and self.record['steps'][-1].get('finished_at') is None:self.record['steps'][-1]['finished_at']=now
        self.record.update(state='running',stage=label,heartbeat=now)
        self.record['steps'].append({'label':label,'started_at':now,'finished_at':None})
        write(self.folder/'status.json',self.record)

    def command(self,args,cwd,timeout=180):
        env=dict(os.environ,GIT_TERMINAL_PROMPT='0',GCM_INTERACTIVE='never',PYTHONIOENCODING='utf-8',PIP_DISABLE_PIP_VERSION_CHECK='1')
        try:
            with (self.folder/'commands.log').open('ab') as output:
                result=subprocess.run(args,cwd=cwd,env=env,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=output,timeout=timeout)
                output.write(result.stdout)
            if result.returncode:raise UpdateError('Не выполнен этап «'+self.record['stage']+'». Проверьте локальный журнал commands.log и доступ Git к приватному репозиторию.')
            return result.stdout.decode('utf-8',errors='replace').strip()
        except (OSError,subprocess.TimeoutExpired) as exc:raise UpdateError('Не выполнен этап «'+self.record['stage']+'»: команда недоступна или превышено время ожидания.') from exc

    def interpreter(self,app):
        signature=requirement_signature(app)
        if self.config.get('requirements_sha256')==signature and Path(self.config.get('python_executable','')).is_file():
            return self.config['python_executable'],signature
        env_dir=self.home/'venvs'/signature[:16]
        python=env_dir/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
        self.command([sys.executable,'-m','venv',str(env_dir)],cwd=self.folder)
        requirement='requirements-portal.txt' if (app/'requirements-portal.txt').is_file() else 'requirements.txt'
        self.command([str(python),'-m','pip','install','-r',requirement],cwd=app,timeout=300)
        return str(python),signature

    def smoke(self,app,python):
        temporary=self.folder/'smoke';temporary.mkdir()
        data=temporary/'data';shutil.copytree(self.config['data_dir'],data)
        with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        config={**self.config,'app_dir':str(app),'data_dir':str(data),'port':port,'python_executable':python,
                'package_sha256':self.record.get('target_release',self.record.get('target_commit'))}
        path=temporary/'runtime.json';write(path,config)
        try:
            ensure_running(path)
            conn=http.client.HTTPConnection('127.0.0.1',port,timeout=30)
            try:
                conn.request('GET','/card-api/meta');result=conn.getresponse();meta=json.loads(result.read())
                if result.status!=200 or not isinstance(meta.get('lines'),list):raise UpdateError('Новая версия не смогла прочитать копию локального снимка.')
                if not meta['lines']:raise UpdateError('В локальном снимке нет линеек для проверки.')
                payload=json.dumps({'mode':'planning','filters':{'line':meta['lines'][0]['name']},'inputs':{}}).encode()
                conn.request('POST','/card-api/card',body=payload,headers={'Content-Type':'application/json','Origin':config['origins'][0]})
                result=conn.getresponse();card=json.loads(result.read())
                if result.status!=200 or not isinstance(card.get('sections'),list):raise UpdateError('Новая версия не смогла рассчитать проверочную карточку.')
            finally:conn.close()
        finally:stop(path)

    def activate(self,app,python,requirements):
        from scripts.split_monitoring_runtime import _ensure_running
        with lock(self.home/'activation.lock'),lock(self.home/'refresh.lock'):
            # Keep proxy-origin changes made during validation, and refuse to
            # overwrite a different release installed concurrently.
            latest=read_config(self.config_path)
            if latest['app_dir']!=self.config['app_dir']:raise UpdateError('Рабочая версия изменилась во время проверки. Повторите обновление.')
            self.config=latest
            stop(self.config_path)
            target={**self.config,'app_dir':str(app),'python_executable':python,'requirements_sha256':requirements,
                    'package_sha256':self.record.get('target_release',self.record.get('target_commit')),'git_commit':self.record.get('target_commit',''),'repository':REPOSITORY,'branch':BRANCH,'release_source':self.record.get('source','git')}
            write(self.home/'previous-runtime.json',self.config)
            try:
                write(self.config_path,target);_ensure_running(self.config_path)
            except Exception:
                try:stop(self.config_path)
                finally:write(self.config_path,self.config);_ensure_running(self.config_path)
                raise UpdateError('Новая версия не запустилась. Предыдущая версия восстановлена.')

    def prepare_package(self):
        from scripts.split_monitoring_package import extract_verified,PackageError
        self.stage('Проверка SHA-256 загруженного ZIP')
        package=self.folder/'package.zip'
        digest=sha256(package.read_bytes()).hexdigest()
        if digest!=self.record['package_sha256']:raise UpdateError('Загруженный ZIP изменился после загрузки.')
        self.record.update(target_release=digest,previous_commit=self.config.get('git_commit',''))
        self.stage('Проверка полного пакета и сборки интерфейса')
        try:root,manifest=extract_verified(package,self.folder/'extracted')
        except PackageError as exc:raise UpdateError(str(exc)) from exc
        app=self.home/'releases'/('zip-'+digest[:16]+'-'+self.record['id'][:8])
        # Packaged data is checked for integrity, but never copied into the
        # active release or the operational data directory.
        shutil.copytree(root,app,ignore=lambda directory,names:[name for name in names if name in ('.git','node_modules','__pycache__','.venv') or (Path(directory)==root and name=='data')])
        verify_build(app,Path(self.config['app_dir']),Path(self.config['data_dir']))
        self.record['package_created_at']=str(manifest.get('createdAt',''))[:100]
        return app

    def run(self):
        try:
            with lock(self.home/'update.lock'):
                if self.record.get('source')=='package':
                    app=self.prepare_package()
                else:
                    self.stage('Получение Split-monitoring из GitHub')
                    checkout=self.folder/'checkout'
                    self.command(['git','-c','core.hooksPath='+str(self.folder/'no-hooks'),'clone','--depth','1','--branch',BRANCH,self.repository,str(checkout)],cwd=self.folder)
                    commit=self.command(['git','rev-parse','HEAD'],cwd=checkout)
                    if not re.fullmatch('[0-9a-f]{40}',commit):raise UpdateError('Git не вернул SHA коммита.')
                    self.record['target_commit']=commit;self.record['previous_commit']=self.config.get('git_commit','')
                    if self.config.get('git_commit')==commit:
                        self.record.update(state='completed',message='Установлена последняя версия мониторинга.');return
                    self.stage('Проверка отдельного релиза и сборки интерфейса')
                    if any(path.is_symlink() for path in checkout.rglob('*')):raise UpdateError('Символические ссылки в релизе не поддерживаются.')
                    app=self.home/'releases'/('git-'+commit[:16]+'-'+self.record['id'][:8])
                    shutil.copytree(checkout,app,ignore=lambda directory,names: [name for name in names if name in ('.git','node_modules','.venv') or (Path(directory)==checkout and name=='data')])
                    if not (app/'portal_wsgi.py').is_file():raise UpdateError('В репозитории нет portal_wsgi.py.')
                    verify_build(app,Path(self.config['app_dir']),Path(self.config['data_dir']))
                self.stage('Подготовка отдельного Python-окружения мониторинга')
                python,requirements=self.interpreter(app)
                self.stage('Проверка запуска на копии рабочих данных')
                self.smoke(app,python)
                self.stage('Переключение только приложения мониторинга')
                self.activate(app,python,requirements)
                self.record.update(state='completed',message='Мониторинг обновлён. Рабочие данные сохранены.')
        except Exception as exc:
            self.record.update(state='failed',message=str(exc) if isinstance(exc,UpdateError) else 'Обновление мониторинга не выполнено. Подробности в локальном worker.log.')
            import logging;logging.exception('Split-monitoring update failed')
        finally:
            now=time.time()
            if self.record['steps'] and self.record['steps'][-1].get('finished_at') is None:self.record['steps'][-1]['finished_at']=now
            self.record.update(finished_at=datetime.now(timezone.utc).isoformat(),heartbeat=now)
            write(self.folder/'status.json',self.record)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--config',type=Path,default=DEFAULT_CONFIG);parser.add_argument('--run',required=True)
    args=parser.parse_args();Updater(args.config,args.run).run()


if __name__=='__main__':main()
