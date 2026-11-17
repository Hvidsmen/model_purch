from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from zipfile import ZipFile
from .split_monitoring_runtime import install


class PrivatePackageTests(unittest.TestCase):
    def package(self,root):
        files={'portal_wsgi.py':'# isolated backend', 'data/template.json':'{}','data/planning_snapshot.json':'{}',
               'data/reviewed_snapshot.json':'{}','app/dist/index.html':'<html>verified</html>', 'app/dist/snapshot.json':'{}'}
        files['app/dist/data-app-build.json']=json.dumps({key:{'path':path,'sha256':sha256(files['app/dist/'+path].encode()).hexdigest()} for key,path in [('html','index.html'),('snapshot','snapshot.json')]})
        manifest={'packageKind':'private-portal-deployment','files':[{'path':name,'sha256':sha256(value.encode()).hexdigest()} for name,value in files.items()]}
        path=root/'package.zip'
        with ZipFile(path,'w') as archive:
            for name,value in {**files,'PACKAGE_MANIFEST.json':json.dumps(manifest)}.items():archive.writestr('rac-line-card/'+name,value)
        return path

    def test_verified_install_keeps_existing_operational_snapshot(self):
        with TemporaryDirectory() as directory:
            root=Path(directory);package=self.package(root);config_path=root/'private/runtime.json'
            config=install(package,['http://localhost:8000'],config_path=config_path)
            self.assertTrue((Path(config['app_dir'])/'app/dist/index.html').exists())
            snapshot=Path(config['data_dir'])/'planning_snapshot.json';snapshot.write_text('{"newer":true}')
            install(package,['http://localhost:8000'],config_path=config_path)
            self.assertEqual(snapshot.read_text(),'{"newer":true}')
            self.assertNotIn('password',config)

    def test_corrupt_or_traversing_archive_is_rejected_before_config_changes(self):
        with TemporaryDirectory() as directory:
            root=Path(directory);package=self.package(root);config=root/'private/runtime.json'
            with ZipFile(package,'a') as archive:archive.writestr('rac-line-card/../escape.txt','bad')
            with self.assertRaises(ValueError):install(package,['http://localhost:8000'],config_path=config)
            self.assertFalse(config.exists());self.assertFalse((root/'escape.txt').exists())
        with TemporaryDirectory() as directory:
            root=Path(directory);package=self.package(root);config=root/'private/runtime.json'
            with ZipFile(package,'a') as archive:archive.writestr('rac-line-card/portal_wsgi.py','tampered')
            with self.assertRaises(ValueError):install(package,['http://localhost:8000'],config_path=config)
            self.assertFalse(config.exists())

    def test_invalid_origin_is_rejected(self):
        with TemporaryDirectory() as directory:
            root=Path(directory)
            for origin in ('file:///tmp/x','http://user:pass@localhost','https://host/path'):
                with self.assertRaises(ValueError):install(self.package(root),[origin],config_path=root/'private/runtime.json')
