from hashlib import sha256
import json
import os
import socket
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from .split_monitoring_update import Updater,UpdateError,write,read,status,start,verify_build,requirement_signature


class IsolatedUpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.home=self.root/'private';self.home.mkdir()
        self.old=self.home/'releases/old';self.old.mkdir(parents=True)
        (self.old/'app/src').mkdir(parents=True);(self.old/'app/src/main.jsx').write_text('// current interface')
        (self.old/'portal_wsgi.py').write_text('# backend')
        (self.old/'requirements-portal.txt').write_text('waitress==3.0.2')
        dist=self.old/'app/dist';dist.mkdir();(dist/'index.html').write_text('<html><body>native</body></html>');(dist/'snapshot.json').write_text('{"reviewed":true}')
        manifest={'kind':'separate-data-v1',**{key:{'path':name,'sha256':sha256((dist/name).read_bytes()).hexdigest()} for key,name in [('html','index.html'),('snapshot','snapshot.json')]}}
        write(dist/'data-app-build.json',manifest)
        self.data=self.home/'data';self.data.mkdir();(self.data/'planning_snapshot.json').write_text('{"working":true}');(self.data/'reviewed_snapshot.json').write_text('{"reviewed":true}')
        self.path=self.home/'runtime.json';self.config={'app_dir':str(self.old),'data_dir':str(self.data),'port':8767,'package_sha256':'a'*64,'control_token':'private','origins':['http://localhost'],'python_executable':sys.executable,'requirements_sha256':requirement_signature(self.old)}
        write(self.path,self.config)
        self.run_id='b'*32;self.record={'id':self.run_id,'actor':1,'state':'queued','heartbeat':0,'steps':[]}
        write(self.home/'updates'/self.run_id/'status.json',self.record)

    def git(self,*args):
        return subprocess.check_output(['git',*args],cwd=self.repo,stderr=subprocess.DEVNULL).decode().strip()

    def repository(self,change_frontend=False):
        self.repo=self.root/'repository';shutil.copytree(self.old,self.repo,ignore=shutil.ignore_patterns('dist'))
        if change_frontend:(self.repo/'app/src/main.jsx').write_text('// new interface')
        (self.repo/'portal_wsgi.py').write_text('# new backend')
        self.git('init','-b','main');self.git('add','.');self.git('-c','user.name=Test','-c','user.email=test@example.invalid','commit','-m','release')
        return str(self.repo)

    def test_backend_only_update_is_isolated_and_preserves_snapshots(self):
        repository=self.repository();before={p.name:p.read_bytes() for p in self.data.iterdir()}
        updater=Updater(self.path,self.run_id,repository=repository)
        def activate(app,python,signature):
            self.assertNotEqual(app,self.old);self.assertTrue((app/'app/dist/index.html').exists())
            self.assertFalse((app/'data').exists());self.assertFalse((app/'.git').exists())
        with patch.object(updater,'smoke') as smoke,patch.object(updater,'activate',side_effect=activate):updater.run()
        self.assertEqual(status(self.path,self.run_id)['state'],'completed');smoke.assert_called_once()
        self.assertEqual(before,{p.name:p.read_bytes() for p in self.data.iterdir()});self.assertEqual(read(self.path),self.config)
        self.assertTrue((self.old/'portal_wsgi.py').exists())

    def test_frontend_change_without_build_never_stops_current_app(self):
        updater=Updater(self.path,self.run_id,repository=self.repository(True))
        with patch.object(updater,'activate') as activate,patch.object(updater,'smoke') as smoke:updater.run()
        self.assertEqual(status(self.path,self.run_id)['state'],'failed')
        self.assertIn('app/dist',status(self.path,self.run_id)['message']);activate.assert_not_called();smoke.assert_not_called()
        self.assertEqual(read(self.path),self.config)

    def test_failed_activation_restores_old_configuration_and_restarts_old(self):
        updater=Updater(self.path,self.run_id);updater.record['target_commit']='c'*40
        called=[]
        def launch(path):
            called.append(read(path)['app_dir'])
            if len(called)==1:raise RuntimeError('new backend failed')
        with patch('scripts.split_monitoring_update.stop') as stop,patch('scripts.split_monitoring_runtime._ensure_running',side_effect=launch):
            with self.assertRaisesRegex(UpdateError,'восстановлена'):updater.activate(self.root/'new',sys.executable,'digest')
        self.assertEqual(read(self.path),self.config);self.assertEqual(called,[str(self.root/'new'),str(self.old)]);self.assertEqual(stop.call_count,2)

    def test_verified_missing_snapshot_hydrated_only_from_exact_local_data(self):
        new=self.root/'new';shutil.copytree(self.old,new)
        (new/'app/dist/snapshot.json').unlink();(self.old/'app/dist/snapshot.json').unlink()
        verify_build(new,self.old,self.data);self.assertEqual((new/'app/dist/snapshot.json').read_bytes(),(self.data/'reviewed_snapshot.json').read_bytes())
        (new/'app/dist/index.html').write_text('tampered')
        with self.assertRaises(UpdateError):verify_build(new,self.old,self.data)

    def test_dispatch_coalesces_running_update(self):
        with patch('scripts.split_monitoring_update.subprocess.Popen') as worker:
            first=start(self.path,1);second=start(self.path,2)
        self.assertEqual(first['id'],second['id']);worker.assert_called_once()
        command=worker.call_args.args[0]
        self.assertIn('split_monitoring_update.py',command[1]);self.assertNotIn('pull',command)

    def test_private_dependencies_never_install_into_portal_interpreter(self):
        updater=Updater(self.path,self.run_id);updater.config.pop('requirements_sha256');commands=[]
        with patch.object(updater,'command',side_effect=lambda args,**kwargs:commands.append((args,kwargs))):
            python,_=updater.interpreter(self.old)
        self.assertNotEqual(python,sys.executable);self.assertIn(str(self.home/'venvs'),python)
        self.assertEqual(commands[0][0][1:3],['-m','venv']);self.assertEqual(commands[1][0][0],python)


    @unittest.skipUnless(os.environ.get('SPLIT_MONITORING_TEST_PACKAGE'),'Requires private local package')
    def test_real_package_staged_checked_and_activated_without_data_changes(self):
        from .split_monitoring_runtime import install,ensure_running,health,stop
        with TemporaryDirectory() as directory:
            root=Path(directory);path=root/'runtime.json'
            with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
            config=install(os.environ['SPLIT_MONITORING_TEST_PACKAGE'],['http://localhost:8000'],config_path=path,port=port)
            old=Path(config['app_dir'])
            config.update(python_executable=sys.executable,requirements_sha256=requirement_signature(old));write(path,config)
            # Local Git fixture from the verified deployment source: a backend
            # change with identical frontend, no dist/data in the repository.
            self.repo=root/'repository'
            shutil.copytree(old,self.repo,ignore=shutil.ignore_patterns('dist','data','__pycache__'))
            with (self.repo/'portal_wsgi.py').open('a') as target:target.write('\n# Backend-only release fixture\n')
            self.git('init','-b','main');self.git('add','--force','.')
            self.git('-c','user.name=Test','-c','user.email=test@example.invalid','commit','-m','backend-only release')
            run_id='d'*32;write(root/'updates'/run_id/'status.json',{'id':run_id,'actor':1,'state':'queued','steps':[]})
            data=Path(config['data_dir']);before={p.name:sha256(p.read_bytes()).hexdigest() for p in data.iterdir() if p.is_file()}
            try:
                ensure_running(path)
                updater=Updater(path,run_id,repository=str(self.repo));updater.run()
                record=status(path,run_id);self.assertEqual(record['state'],'completed',record.get('message'))
                current=read(path);self.assertEqual(current['git_commit'],self.git('rev-parse','HEAD'))
                self.assertTrue(health(current));self.assertNotEqual(current['app_dir'],config['app_dir'])
                self.assertEqual(before,{p.name:sha256(p.read_bytes()).hexdigest() for p in data.iterdir() if p.is_file()})
                self.assertTrue((old/'portal_wsgi.py').exists())
            finally:stop(path)

    def test_activation_does_not_interrupt_database_refresh(self):
        from .split_monitoring_runtime import lock
        updater=Updater(self.path,self.run_id);updater.record['target_commit']='f'*40
        with lock(self.home/'refresh.lock'),patch('scripts.split_monitoring_update.stop') as stop:
            with self.assertRaises(OSError):updater.activate(self.root/'new',sys.executable,'digest')
            stop.assert_not_called()
        self.assertEqual(read(self.path),self.config)

    def test_unhealthy_candidate_child_is_terminated_before_rollback(self):
        from .split_monitoring_runtime import _ensure_running,Unavailable
        with patch('scripts.split_monitoring_runtime.health',return_value=False),patch('scripts.split_monitoring_runtime.time.monotonic',side_effect=[0,11]),patch('scripts.split_monitoring_runtime.subprocess.Popen') as spawn:
            spawn.return_value.poll.return_value=None
            with self.assertRaises(Unavailable):_ensure_running(self.path)
        spawn.return_value.terminate.assert_called_once();spawn.return_value.wait.assert_called_once_with(timeout=5)
