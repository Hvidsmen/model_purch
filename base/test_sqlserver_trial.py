from datetime import datetime,timezone,timedelta
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import MagicMock,patch
import sqlite3
from django.contrib.auth.models import User,Group
from django.core.exceptions import ImproperlyConfigured,ValidationError
from django.test import SimpleTestCase,TestCase,RequestFactory,override_settings
from portal.sqlserver_trial_config import database_config,source_config,redact
from portal.sqlserver_trial import SourceReadOnlyRouter,TrialIsolationMiddleware
from portal.trial_transfer import canonical,digest_model,require_trial,clear_migration_seeds
from scripts.sqlserver_trial import backup_sqlite


class TrialConfigurationTests(SimpleTestCase):
    def test_test_database_is_explicit_and_production_names_are_rejected(self):
        config=database_config({'PORTAL_TRIAL_SERVER':'vm-dwh'})
        self.assertEqual(config['ENGINE'],'mssql')
        self.assertEqual(config['NAME'],'Portal_Test')
        self.assertEqual(config['USER'],'')
        self.assertNotIn('MS_SQL_CONN_STR',config)
        for name in ('DataWH','ModelPurch','master','Portal','Portal_Test;DROP DATABASE DataWH'):
            with self.assertRaises(ImproperlyConfigured):
                database_config({'PORTAL_TRIAL_SERVER':'vm-dwh','PORTAL_TRIAL_DATABASE':name})
    def test_unsafe_host_and_driver_are_rejected(self):
        with self.assertRaises(ImproperlyConfigured):database_config({'PORTAL_TRIAL_SERVER':'vm-dwh;DATABASE=DataWH'})
        with self.assertRaises(ImproperlyConfigured):database_config({'PORTAL_TRIAL_SERVER':'vm-dwh','PORTAL_TRIAL_DRIVER':'unapproved'})
    def test_source_alias_is_really_read_only(self):
        with TemporaryDirectory() as folder:
            path=Path(folder)/'source.sqlite3'
            with sqlite3.connect(path) as connection:connection.execute('CREATE TABLE item (id INTEGER)')
            config=source_config({'PORTAL_TRIAL_SQLITE_SNAPSHOT':str(path)})
            with sqlite3.connect(config['NAME'],uri=True) as connection:
                with self.assertRaises(sqlite3.OperationalError):connection.execute('INSERT INTO item VALUES(1)')
            with self.assertRaises(ImproperlyConfigured):source_config({'PORTAL_TRIAL_SQLITE_SNAPSHOT':str(Path(folder)/'missing')})
    def test_secret_redaction_including_odbc_escaped_braces(self):
        message=redact("08001 PWD={first}}last};UID=admin;SERVER=vm-dwh")
        self.assertIn('08001',message)
        for text in ('first','last','admin','vm-dwh'):self.assertNotIn(text,message)
    @override_settings(PORTAL_SQLSERVER_TRIAL=False)
    def test_transfer_command_cannot_touch_normal_portal(self):
        with self.assertRaises(ValidationError):require_trial()
    @override_settings(PORTAL_SQLSERVER_TRIAL=True)
    def test_external_sql_connections_are_disabled(self):
        from model_purch.conns import connection_string
        with self.assertRaises(RuntimeError):connection_string('vm-dwh','DataWH')
        with self.assertRaises(RuntimeError):connection_string('vm-dwh','ModelPurch')
    def test_trial_blocks_deployment_exports_and_private_app_proxy(self):
        middleware=TrialIsolationMiddleware(lambda request:None)
        factory=RequestFactory()
        for name in ('portal_update_start','export_scenario_to_sql','split_monitoring_api','portal_database_diagnostics'):
            request=factory.post('/');request.resolver_match=SimpleNamespace(url_name=name)
            self.assertEqual(middleware.process_view(request,None,[],{}).status_code,403)
        request=factory.post('/');request.resolver_match=SimpleNamespace(url_name='model_ddp_detail')
        self.assertIsNone(middleware.process_view(request,None,[],{}))
        self.assertFalse(SourceReadOnlyRouter().allow_migrate('trial_source','base'))
    @override_settings(PORTAL_SQLSERVER_TRIAL=True,PORTAL_TRIAL_RUN_ID='expected')
    def test_wrong_destination_marker_is_rejected_before_any_write(self):
        connection=MagicMock(vendor='microsoft',settings_dict={'NAME':'Portal_Test'})
        cursor=connection.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value=('Portal_Test','different-run')
        with patch('portal.trial_transfer.connections',{'default':connection}):
            with self.assertRaises(ValidationError):clear_migration_seeds()
        self.assertEqual(cursor.execute.call_count,1)
        connection.constraint_checks_disabled.assert_not_called()
    @override_settings(PORTAL_SQLSERVER_TRIAL=True,PORTAL_TRIAL_RUN_ID='expected')
    def test_imported_trial_cannot_be_cleared_again(self):
        connection=MagicMock(vendor='microsoft',settings_dict={'NAME':'Portal_Test'})
        cursor=connection.cursor.return_value.__enter__.return_value
        cursor.fetchone.side_effect=[('Portal_Test','expected'),('verified',)]
        with patch('portal.trial_transfer.connections',{'default':connection}):
            with self.assertRaises(ValidationError):clear_migration_seeds()
        connection.constraint_checks_disabled.assert_not_called()


class SnapshotTests(SimpleTestCase):
    def test_backup_contains_committed_wal_data_without_changing_source(self):
        with TemporaryDirectory() as folder:
            source=Path(folder)/'live.sqlite3';target=Path(folder)/'copy.sqlite3'
            live=sqlite3.connect(source)
            try:
                live.execute('PRAGMA journal_mode=WAL')
                live.execute('CREATE TABLE payload (id INTEGER PRIMARY KEY, value TEXT)')
                live.execute('INSERT INTO payload VALUES(5,?)',('Данные из WAL',));live.commit()
                original=source.read_bytes()
                backup_sqlite(source,target)
                self.assertEqual(source.read_bytes(),original)
                with sqlite3.connect(target) as copied:
                    self.assertEqual(copied.execute('SELECT id,value FROM payload').fetchone(),(5,'Данные из WAL'))
                with self.assertRaises(RuntimeError):backup_sqlite(source,target)
                with self.assertRaises(RuntimeError):backup_sqlite(source,source)
            finally:live.close()
    def test_canonical_values_preserve_semantics(self):
        a=datetime(2026,11,24,10,tzinfo=timezone(timedelta(hours=3)))
        b=datetime(2026,11,24,7,tzinfo=timezone.utc)
        self.assertEqual(canonical(a),canonical(b))
        self.assertEqual(canonical(Decimal('1.000')),canonical(Decimal('1')))
        self.assertEqual(canonical(memoryview(b'\x00\xff')),{'hex':'00ff'})


class TransferDigestTests(TestCase):
    def test_join_table_digest_ignores_surrogate_pk_but_keeps_membership(self):
        user=User.objects.create_user('trial-digest')
        first=Group.objects.create(name='first-trial-group');second=Group.objects.create(name='second-trial-group')
        user.groups.add(first)
        through=User.groups.through
        original=digest_model(through,'default')
        through.objects.filter(user=user,group=first).update(id=9999)
        self.assertEqual(digest_model(through,'default'),original)
        user.groups.add(second)
        self.assertNotEqual(digest_model(through,'default'),original)

class TrialExportEncodingTests(SimpleTestCase):
    def test_child_python_forces_utf8_for_files_and_console(self):
        import os,sys
        from scripts.sqlserver_trial import execute
        with TemporaryDirectory() as directory:
            target=Path(directory)/'export.json'
            command=[sys.executable,'-c',"import sys,json; assert sys.flags.utf8_mode==1; open(sys.argv[1],'w').write(json.dumps({'name':'Модель\\u2010测试'},ensure_ascii=False))",str(target)]
            execute(command,{**os.environ,'PYTHONUTF8':'0','PYTHONIOENCODING':'cp1251'})
            import json
            self.assertEqual(json.loads(target.read_text(encoding='utf-8')),{'name':'Модель‐测试'})

    def test_resume_uses_complete_copies_without_backing_up_again(self):
        import json
        from scripts.sqlserver_trial import resumable_snapshot
        with TemporaryDirectory() as directory:
            base=Path(directory);workspace=base/'20261124-001'
            workspace.mkdir();source=base/'live.sqlite3'
            connection=sqlite3.connect(source);connection.execute('CREATE TABLE example(id INTEGER)');connection.commit();connection.close()
            backup_sqlite(source,workspace/'source-original.sqlite3')
            backup_sqlite(source,workspace/'source.sqlite3')
            metadata={'database':'Portal_Test','server':'vm-dwh','run_id':'trial','source_sqlite':str(source)}
            (workspace/'run.json').write_text(json.dumps(metadata),encoding='utf-8')
            with patch('scripts.sqlserver_trial.backup_sqlite',side_effect=AssertionError('must not back up')):
                found,saved=resumable_snapshot(base)
            self.assertEqual(found,workspace)
            self.assertEqual(saved,metadata)
            (workspace/'source.sqlite3').unlink()
            with self.assertRaisesRegex(RuntimeError,'source.sqlite3'):resumable_snapshot(base)

    def test_resume_refuses_live_database_and_corrupt_copy(self):
        import json
        from scripts.sqlserver_trial import resumable_snapshot
        with TemporaryDirectory() as directory:
            base=Path(directory);workspace=base/'run';workspace.mkdir()
            source=workspace/'source.sqlite3'
            connection=sqlite3.connect(source);connection.execute('CREATE TABLE example(id INTEGER)');connection.commit();connection.close()
            backup_sqlite(source,workspace/'source-original.sqlite3')
            metadata={'database':'Portal_Test','server':'vm-dwh','run_id':'trial','source_sqlite':str(source)}
            (workspace/'run.json').write_text(json.dumps(metadata),encoding='utf-8')
            with self.assertRaisesRegex(RuntimeError,'отдельной'):resumable_snapshot(base)
            metadata['source_sqlite']=str(base/'other.sqlite3')
            (workspace/'run.json').write_text(json.dumps(metadata),encoding='utf-8')
            source.write_bytes(b'broken database')
            with self.assertRaises(sqlite3.DatabaseError):resumable_snapshot(base)
