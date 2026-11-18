from hashlib import sha256
import json
import os
from pathlib import Path
import socket
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from zipfile import ZipFile
from django.core.files.uploadedfile import SimpleUploadedFile
from .split_monitoring_package import extract_verified,PackageError
from .split_monitoring_update import start,status,Updater,write,read,requirement_signature


class PackageDeploymentTests(unittest.TestCase):
    def package(self,root,*,extra=None,tamper=False):
        files={'portal_wsgi.py':'# backend','snapshot_store.py':'# current module','requirements-portal.txt':'waitress==3.0.2',
               'data/template.json':'{}','data/planning_snapshot.json':'{"from_package":true}', 'data/reviewed_snapshot.json':'{}',
               'app/dist/index.html':'<html><body>new</body></html>','app/dist/snapshot.json':'{}'}
        files['app/dist/data-app-build.json']=json.dumps({'kind':'separate-data-v1',**{key:{'path':name,'sha256':sha256(files['app/dist/'+name].encode()).hexdigest()} for key,name in [('html','index.html'),('snapshot','snapshot.json')]}})
        manifest={'packageKind':'private-portal-deployment','createdAt':'2026-11-18','files':[{'path':name,'sha256':sha256(value.encode()).hexdigest()} for name,value in files.items()]}
        if tamper:files['snapshot_store.py']='changed after verification'
        path=root/'package.zip'
        with ZipFile(path,'w') as archive:
            for name,value in {**files,'PACKAGE_MANIFEST.json':json.dumps(manifest),**(extra or {})}.items():archive.writestr('rac-line-card/'+name,value)
        return path

    def setup_runtime(self,root):
        old=root/'old';old.mkdir();(old/'requirements-portal.txt').write_text('waitress==3.0.2')
        data=root/'data';data.mkdir();(data/'planning_snapshot.json').write_text('{"newer_server_snapshot":true}')
        path=root/'runtime.json';write(path,{'app_dir':str(old),'data_dir':str(data),'port':8767,'package_sha256':'a'*64,'control_token':'secret','origins':['https://da-dev.daichi.ru'],'python_executable':sys.executable,'requirements_sha256':requirement_signature(old)})
        return path,data

    def test_package_checks_all_files_and_updates_modules_without_data_overwrite(self):
        with TemporaryDirectory() as directory:
            root=Path(directory);path,data=self.setup_runtime(root);package=self.package(root)
            upload=SimpleUploadedFile('private.zip',package.read_bytes())
            with patch('scripts.split_monitoring_update.subprocess.Popen'):job=start(path,3,uploaded_file=upload,expected_sha256=sha256(package.read_bytes()).hexdigest())
            updater=Updater(path,job['id'])
            with patch.object(updater,'command') as git,patch.object(updater,'smoke') as smoke,patch.object(updater,'activate') as activate:updater.run()
            record=status(path,job['id']);self.assertEqual(record['state'],'completed',record.get('message'))
            git.assert_not_called();smoke.assert_called_once();activate.assert_called_once()
            app=activate.call_args.args[0]
            self.assertEqual((app/'snapshot_store.py').read_text(),'# current module');self.assertFalse((app/'data').exists())
            self.assertEqual((data/'planning_snapshot.json').read_text(),'{"newer_server_snapshot":true}')
            self.assertEqual(record['source'],'package');self.assertNotIn('target_commit',record)

    def test_corrupt_package_never_stops_running_app(self):
        with TemporaryDirectory() as directory:
            root=Path(directory);path,data=self.setup_runtime(root);package=self.package(root,tamper=True)
            with patch('scripts.split_monitoring_update.subprocess.Popen'):job=start(path,1,uploaded_file=SimpleUploadedFile('private.zip',package.read_bytes()))
            updater=Updater(path,job['id'])
            with patch.object(updater,'activate') as activate,patch.object(updater,'smoke') as smoke:updater.run()
            self.assertEqual(status(path,job['id'])['state'],'failed');activate.assert_not_called();smoke.assert_not_called()
            self.assertEqual(read(path)['app_dir'],str(root/'old'))

    def test_wrong_outer_checksum_is_rejected_before_dispatch(self):
        with TemporaryDirectory() as directory:
            root=Path(directory);path,_=self.setup_runtime(root)
            with patch('scripts.split_monitoring_update.subprocess.Popen') as spawn:
                with self.assertRaises(PackageError):start(path,1,uploaded_file=SimpleUploadedFile('private.zip',b'ZIP'),expected_sha256='f'*64)
            spawn.assert_not_called();self.assertFalse((root/'updates/current.json').exists())
            self.assertEqual(list((root/'updates').glob('*/package.zip')),[])

    def test_traversal_and_unlisted_modules_are_rejected(self):
        for extra in ({'../../escape.py':'bad'},{'CON.txt':'bad'},{'snapshot_store.PY':'bad'},{'unlisted.py':'bad'}):
            with self.subTest(extra=extra),TemporaryDirectory() as directory:
                root=Path(directory);package=self.package(root,extra=extra)
                with self.assertRaises(PackageError):extract_verified(package,root/'stage')
                self.assertFalse((root/'escape.py').exists())

    @unittest.skipUnless(os.environ.get('SPLIT_MONITORING_TEST_PACKAGE'),'Requires private local package')
    def test_real_uploaded_package_runs_on_copied_data_and_switches_backend(self):
        from .split_monitoring_runtime import install,ensure_running,health,stop
        with TemporaryDirectory() as directory:
            root=Path(directory);path=root/'runtime.json'
            with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
            package=Path(os.environ['SPLIT_MONITORING_TEST_PACKAGE'])
            config=install(package,['http://localhost:8000'],config_path=path,port=port)
            config.update(python_executable=sys.executable,requirements_sha256=requirement_signature(Path(config['app_dir'])));write(path,config)
            data=Path(config['data_dir']);before={p.name:sha256(p.read_bytes()).hexdigest() for p in data.iterdir() if p.is_file()}
            with patch('scripts.split_monitoring_update.subprocess.Popen'):job=start(path,1,uploaded_file=SimpleUploadedFile('private.zip',package.read_bytes()))
            try:
                ensure_running(path);Updater(path,job['id']).run()
                record=status(path,job['id']);self.assertEqual(record['state'],'completed',record.get('message'))
                current=read(path);self.assertTrue(health(current));self.assertEqual(current['release_source'],'package')
                self.assertEqual(current['git_commit'],'');self.assertEqual(current['origins'],config['origins'])
                self.assertEqual(current['control_token'],config['control_token'])
                self.assertEqual(before,{p.name:sha256(p.read_bytes()).hexdigest() for p in data.iterdir() if p.is_file()})
                self.assertTrue((Path(config['app_dir'])/'portal_wsgi.py').exists())
            finally:stop(path)
