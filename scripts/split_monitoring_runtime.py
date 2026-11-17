"""Install and run the private Split-monitoring package outside the portal checkout.

Only the loopback worker imports the application's flat Python modules. The
Django process never adds the external repository to its import path.
"""
from contextlib import contextmanager
from hashlib import sha256
import argparse
import hmac
import http.client
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from urllib.parse import urlsplit
from zipfile import ZipFile

ROOT=Path(__file__).resolve().parents[1]
DEFAULT_CONFIG=ROOT/'.local/split-monitoring/runtime.json'
MAX_PACKAGE_BYTES=200*1024*1024


class Unavailable(Exception):pass


def read_config(path=DEFAULT_CONFIG):
    try:data=json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError,ValueError) as exc:raise Unavailable('Пакет мониторинга ещё не установлен.') from exc
    if not isinstance(data,dict):raise Unavailable('Некорректная конфигурация мониторинга.')
    if not isinstance(data.get('port'),int) or not 1024<=data['port']<=65535:raise Unavailable('Некорректный порт мониторинга.')
    for field in ('app_dir','data_dir','package_sha256','control_token','origins'):
        if not data.get(field):raise Unavailable('Не заполнена конфигурация мониторинга.')
    return data


@contextmanager
def lock(path):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    handle=path.open('a+b');locked=False
    try:
        if os.name=='nt':
            import msvcrt
            if handle.seek(0,2)==0:handle.write(b'0');handle.flush()
            handle.seek(0);msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
        else:
            import fcntl
            fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
        locked=True;yield
    finally:
        if locked:
            if os.name=='nt':handle.seek(0);msvcrt.locking(handle.fileno(),msvcrt.LK_UNLCK,1)
            else:fcntl.flock(handle,fcntl.LOCK_UN)
        handle.close()


def health(config):
    conn=http.client.HTTPConnection('127.0.0.1',config['port'],timeout=.5)
    try:
        conn.request('GET','/card-api/health')
        result=conn.getresponse();payload=result.read(4096)
        return result.status==200 and result.getheader('X-Portal-Split-Release')==config['package_sha256'] and json.loads(payload).get('status')=='ok'
    except (OSError,ValueError,http.client.HTTPException):return False
    finally:conn.close()


def ensure_running(config_path=DEFAULT_CONFIG):
    config=read_config(config_path)
    if health(config):return config
    home=Path(config_path).resolve().parent
    deadline=time.monotonic()+10
    while True:
        try:
            with lock(home/'launch.lock'):
                config=read_config(config_path)
                if health(config):return config
                log=(home/'backend.log').open('ab')
                args=[sys.executable,str(Path(__file__).resolve()),'serve','--config',str(Path(config_path).resolve())]
                options={'creationflags':subprocess.CREATE_NO_WINDOW} if os.name=='nt' else {'start_new_session':True}
                try:subprocess.Popen(args,cwd=config['app_dir'],stdin=subprocess.DEVNULL,stdout=log,stderr=log,**options)
                finally:log.close()
                while time.monotonic()<deadline:
                    if health(config):return config
                    time.sleep(.15)
                raise Unavailable('Мониторинг не запустился. Подробности в локальном журнале backend.log.')
        except OSError as exc:
            if time.monotonic()>=deadline:raise Unavailable('Мониторинг запускается другим процессом. Повторите открытие.') from exc
            time.sleep(.15)


def install(package,origins,*,config_path=DEFAULT_CONFIG,port=8767):
    home=Path(config_path).resolve().parent
    with lock(home/'launch.lock'):
        return _install(package,origins,config_path=config_path,port=port)


def _install(package,origins,*,config_path,port):
    config_path=Path(config_path).resolve();home=config_path.parent
    for origin in origins:
        parsed=urlsplit(origin)
        if parsed.scheme not in ('http','https') or not parsed.netloc or parsed.path not in ('','/') or parsed.query or parsed.fragment or parsed.username:
            raise ValueError('Укажите полный origin портала без пути, например http://192.168.221.178:8000')
    if not origins:raise ValueError('Не указан адрес портала.')
    if config_path.exists():
        previous=read_config(config_path)
        if health(previous):raise ValueError('Перед заменой пакета остановите backend: split_monitoring_runtime.py stop')
    home.mkdir(parents=True,exist_ok=True,mode=0o700)
    if os.name!='nt':home.chmod(0o700)
    package=Path(package);digest=sha256(package.read_bytes()).hexdigest()
    destination=home/'releases'/digest[:16]
    with tempfile.TemporaryDirectory(prefix='split-install-',dir=home) as temporary:
        stage=Path(temporary)
        with ZipFile(package) as archive:
            if sum(item.file_size for item in archive.infolist())>MAX_PACKAGE_BYTES:raise ValueError('Распакованный пакет слишком большой.')
            for item in archive.infolist():
                target=(stage/item.filename).resolve()
                if '..' in Path(item.filename).parts or not target.is_relative_to(stage.resolve()) or '\\' in item.filename or (item.external_attr>>16)&0o170000==0o120000:
                    raise ValueError('Некорректный путь в архиве.')
            archive.extractall(stage)
        manifests=list(stage.glob('*/PACKAGE_MANIFEST.json'))+list(stage.glob('PACKAGE_MANIFEST.json'))
        if len(manifests)!=1:raise ValueError('В архиве должен быть один PACKAGE_MANIFEST.json.')
        app=manifests[0].parent;manifest=json.loads(manifests[0].read_text(encoding='utf-8'))
        if manifest.get('packageKind')!='private-portal-deployment':raise ValueError('Нужен полный приватный пакет размещения.')
        for item in manifest['files']:
            target=(app/item['path']).resolve()
            if not target.is_relative_to(app.resolve()) or not target.is_file() or sha256(target.read_bytes()).hexdigest()!=item['sha256']:
                raise ValueError('Контрольная сумма пакета не совпала: '+item['path'])
        for name in ('portal_wsgi.py','data/template.json','data/planning_snapshot.json','data/reviewed_snapshot.json','app/dist/data-app-build.json'):
            if not (app/name).is_file():raise ValueError('В пакете отсутствует '+name)
        build=json.loads((app/'app/dist/data-app-build.json').read_text(encoding='utf-8'))
        for key in ('html','snapshot'):
            target=(app/'app/dist'/build[key]['path']).resolve()
            if not target.is_relative_to((app/'app/dist').resolve()) or sha256(target.read_bytes()).hexdigest()!=build[key]['sha256']:
                raise ValueError('Контрольная сумма сборки не совпала.')
        destination.parent.mkdir(exist_ok=True)
        if not destination.exists():shutil.copytree(app,destination)
        data_dir=home/'data';data_dir.mkdir(exist_ok=True)
        for name in ('template.json','planning_snapshot.json','reviewed_snapshot.json'):
            if not (data_dir/name).exists():shutil.copy2(app/'data'/name,data_dir/name)
    if not 1024<=int(port)<=65535:raise ValueError('Порт должен быть от 1024 до 65535.')
    config={'version':1,'app_dir':str(destination),'data_dir':str(data_dir),'port':int(port),
            'origins':[origin.rstrip('/') for origin in origins],'package_sha256':digest,'control_token':secrets.token_urlsafe(32)}
    temporary=config_path.with_suffix('.tmp');temporary.write_text(json.dumps(config,ensure_ascii=False,indent=2),encoding='utf-8')
    try:temporary.chmod(0o600)
    except OSError:pass
    temporary.replace(config_path)
    return config


def serve(config_path):
    config=read_config(config_path);home=Path(config_path).resolve().parent
    with lock(home/'service.lock'):
        os.environ['LINE_CARD_DATA_DIR']=config['data_dir']
        os.environ['LINE_CARD_DIST_DIR']=str(Path(config['app_dir'])/'app/dist')
        os.environ['LINE_CARD_ALLOWED_ORIGINS']=','.join(config['origins'])
        sys.path.insert(0,config['app_dir'])
        import portal_wsgi
        portal_wsgi.verify_build()
        from waitress import create_server,wasyncore
        import logging
        logging.basicConfig(level=logging.INFO)
        def shutdown():
            server.close();wasyncore.close_all(server._map)
            server.task_dispatcher.shutdown()
        def application(environ,start_response):
            if environ.get('PATH_INFO')=='/__portal_control/stop':
                if not hmac.compare_digest(environ.get('HTTP_X_PORTAL_CONTROL',''),config['control_token']):
                    start_response('404 Not Found',[('Content-Length','0')]);return [b'']
                body=b'{"status":"stopping"}'
                start_response('200 OK',[('Content-Type','application/json'),('Content-Length',str(len(body)))])
                threading.Timer(.2,shutdown).start();return [body]
            def start(status,headers,exc_info=None):
                return start_response(status,[*headers,('X-Portal-Split-Release',config['package_sha256'])],exc_info)
            return portal_wsgi.application(environ,start)
        server=create_server(application,host='127.0.0.1',port=config['port'],threads=8,channel_timeout=120,max_request_body_size=20000)
        server.run()


def stop(config_path):
    config=read_config(config_path)
    if not health(config):return
    conn=http.client.HTTPConnection('127.0.0.1',config['port'],timeout=5)
    try:
        conn.request('POST','/__portal_control/stop',headers={'X-Portal-Control':config['control_token']})
        response=conn.getresponse();response.read()
        if response.status!=200:raise Unavailable('Не удалось остановить backend.')
    finally:conn.close()
    for _ in range(100):
        if not health(config):
            try:
                with lock(Path(config_path).resolve().parent/'service.lock'):return
            except OSError:pass
        time.sleep(.1)
    raise Unavailable('Backend ещё останавливается.')


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('action',choices=('install','start','stop','status','serve'))
    parser.add_argument('--config',type=Path,default=DEFAULT_CONFIG);parser.add_argument('--package',type=Path)
    parser.add_argument('--portal-origin',action='append',default=[]);parser.add_argument('--port',type=int,default=8767)
    args=parser.parse_args()
    if args.action=='install':
        if not args.package:parser.error('--package обязателен для install')
        install(args.package,args.portal_origin,config_path=args.config,port=args.port);print('Пакет установлен. Backend запустится при открытии раздела.')
    elif args.action=='serve':serve(args.config)
    elif args.action=='start':ensure_running(args.config);print('Мониторинг запущен на loopback.')
    elif args.action=='stop':stop(args.config);print('Мониторинг остановлен.')
    else:print('Готов' if health(read_config(args.config)) else 'Backend не запущен')


if __name__=='__main__':main()
