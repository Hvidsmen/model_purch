from importlib import import_module
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4
import json,sqlite3
from django.test import SimpleTestCase
from django.db.migrations.loader import MigrationLoader
from scripts.sqlserver_trial import check_indexed_power,retarget_snapshot


class PowerIndexMigrationTests(SimpleTestCase):
    def operation(self):
        migration=import_module('base.migrations.0009_salesplanninggroup_salesplanningblockpair').Migration('0009','base')
        return next(op for op in migration.operations if op.name=='SalesPlanningBlockPair')

    def test_sqlserver_gets_indexable_type_without_mutating_historical_or_sqlite_state(self):
        state=MigrationLoader(None).project_state(('base','0009_salesplanninggroup_salesplanningblockpair'))
        for vendor,kind in [('microsoft','CharField'),('sqlite','TextField')]:
            editor=SimpleNamespace(connection=SimpleNamespace(vendor=vendor,alias='default'),create_model=Mock())
            self.operation().database_forwards('base',editor,state,state)
            model=editor.create_model.call_args.args[0]
            field=model._meta.get_field('power')
            self.assertEqual(field.get_internal_type(),kind)
            if vendor=='microsoft':self.assertEqual(field.max_length,255)
            self.assertEqual(model._meta.constraints[0].fields,('group','power'))
            self.assertEqual(state.apps.get_model('base','SalesPlanningBlockPair')._meta.get_field('power').get_internal_type(),'TextField')

    def test_mssql_backend_generates_bounded_column_and_unique_constraint_without_a_server(self):
        from mssql.base import DatabaseWrapper
        from mssql.schema import DatabaseSchemaEditor
        state=MigrationLoader(None).project_state(('base','0009_salesplanninggroup_salesplanningblockpair'))
        editor=SimpleNamespace(connection=SimpleNamespace(vendor='microsoft',alias='default'),create_model=Mock())
        self.operation().database_forwards('base',editor,state,state)
        model=editor.create_model.call_args.args[0]
        connection=DatabaseWrapper({'ENGINE':'mssql','NAME':'Portal_Test_offline','OPTIONS':{},'TIME_ZONE':None,'AUTOCOMMIT':True},alias='offline')
        connection.__dict__['sql_server_version']=2019
        with DatabaseSchemaEditor(connection,collect_sql=True,atomic=False) as schema:
            schema.create_model(model)
        sql='\n'.join(schema.collected_sql)
        self.assertIn('[power] nvarchar(255)',sql)
        self.assertNotIn('[power] nvarchar(max)',sql)
        self.assertIn('unique_split_group_power_pair',sql)
        self.assertIn('([group_id], [power])',sql)


class TrialRetryTests(SimpleTestCase):
    def test_retarget_preserves_old_run_and_reuses_snapshot_without_deleting_anything(self):
        with TemporaryDirectory() as folder:
            workspace=Path(folder);metadata={'database':'Portal_Test','run_id':uuid4().hex,'server':'vm-dwh','source_sqlite':'live.sqlite3'}
            (workspace/'run.json').write_text(json.dumps(metadata),encoding='utf-8')
            (workspace/'source.sqlite3').write_bytes(b'unchanged')
            result=retarget_snapshot(workspace,metadata,'Portal_Test_powerfix')
            self.assertEqual(metadata['database'],'Portal_Test')
            self.assertEqual(result['database'],'Portal_Test_powerfix')
            self.assertNotEqual(result['run_id'],metadata['run_id'])
            self.assertEqual(json.loads((workspace/('run-'+metadata['run_id']+'.json')).read_text()),metadata)
            self.assertEqual(json.loads((workspace/'run.json').read_text()),result)
            self.assertEqual((workspace/'source.sqlite3').read_bytes(),b'unchanged')

    def test_production_same_or_verified_destination_cannot_be_retargeted(self):
        from django.core.exceptions import ImproperlyConfigured
        with TemporaryDirectory() as folder:
            workspace=Path(folder);metadata={'database':'Portal_Test','run_id':uuid4().hex}
            for name in ('DataWH','ModelPurch'):
                with self.assertRaises(ImproperlyConfigured):retarget_snapshot(workspace,metadata,name)
            with self.assertRaises(RuntimeError):retarget_snapshot(workspace,metadata,'Portal_Test')
            (workspace/'verified.json').write_text('{}')
            with self.assertRaises(RuntimeError):retarget_snapshot(workspace,metadata,'Portal_Test_other')

    def test_oversized_key_is_rejected_without_truncating_snapshot_including_unicode(self):
        with TemporaryDirectory() as folder:
            workspace=Path(folder);snapshot=workspace/'source.sqlite3'
            with sqlite3.connect(snapshot) as db:
                db.execute('CREATE TABLE base_salesplanningblockpair(power TEXT)')
                db.execute('INSERT INTO base_salesplanningblockpair VALUES(?)',('Мощность 09',))
            check_indexed_power(workspace)
            for value in ('x'*256,'😀'*128):
                with sqlite3.connect(snapshot) as db:
                    db.execute('DELETE FROM base_salesplanningblockpair')
                    db.execute('INSERT INTO base_salesplanningblockpair VALUES(?)',(value,))
                with self.assertRaisesRegex(RuntimeError,'не обрезаны'):check_indexed_power(workspace)
                with sqlite3.connect(snapshot) as db:self.assertEqual(db.execute('SELECT power FROM base_salesplanningblockpair').fetchone()[0],value)
