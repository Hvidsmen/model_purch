from base.testing import authorize_test_case, authorize_admin
import json
import os
import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from django.test import SimpleTestCase, TestCase, override_settings, Client
from django.urls import reverse

from portal.local_config import database_path
from .models import ScenarioModel, ScenarioExport, AlgorithmRun, AlgorithmStep, PGGoods, KindPurch, Purch, PurchPay
from .services.backups import backup_sqlite
from .services.preflight import snapshot, fingerprint, validation_errors


class LocalDatabaseTests(SimpleTestCase):
    def test_backup_closes_connections_and_preserves_original_failure(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source_path = root / 'source.sqlite3'
            source_path.touch()
            source, target = Mock(), Mock()
            target.execute.return_value.fetchone.return_value = ('ok',)
            with patch('model_purch.services.backups.sqlite3.connect', side_effect=[source, target]):
                backup_sqlite(source_path, root / 'backups')
            source.close.assert_called_once()
            target.close.assert_called_once()
            source.reset_mock()
            target.reset_mock()
            source.backup.side_effect = RuntimeError('Исходная ошибка копирования')
            def locked_file(*args, **kwargs):
                source.close.assert_called_once()
                target.close.assert_called_once()
                raise PermissionError('Файл занят сторонним процессом')
            with patch('model_purch.services.backups.sqlite3.connect', side_effect=[source, target]), patch('model_purch.services.backups.Path.unlink', side_effect=locked_file):
                with self.assertRaisesRegex(RuntimeError, 'Исходная ошибка копирования'):
                    backup_sqlite(source_path, root / 'backups')

    def test_backup_reports_copy_and_check_and_verifies_data(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'live.sqlite3'
            with sqlite3.connect(source) as database:
                database.execute('CREATE TABLE example (value TEXT)')
                database.execute('INSERT INTO example VALUES (?)', ('x' * 3000000,))
                database.commit()
            output = []
            destination = backup_sqlite(source, root / 'backups', notify=output.append)
            self.assertTrue(any('100.0%' in line for line in output))
            self.assertTrue(any('Starting integrity check' in line for line in output))
            self.assertIn('Backup verified', output[-1])
            with sqlite3.connect(destination) as restored:
                self.assertEqual(restored.execute('SELECT length(value) FROM example').fetchone()[0], 3000000)

    def test_backup_reports_busy_status_and_throttles_progress(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source_path = root / 'source.sqlite3'
            source_path.touch()
            source, target = Mock(), Mock()
            target.execute.return_value.fetchone.return_value = ('ok',)
            def copy(target, pages, progress):
                progress(5, 100, 100)  # SQLITE_BUSY
                progress(0, 50, 100)  # SQLITE_OK
                progress(101, 0, 100)  # SQLITE_DONE
            source.backup.side_effect = copy
            output = []
            with patch('model_purch.services.backups.sqlite3.connect', side_effect=[source, target]), patch('model_purch.services.backups.monotonic', return_value=100):
                backup_sqlite(source_path, root / 'backups', notify=output.append)
            self.assertTrue(any('waiting for SQLite lock' in line for line in output))
            self.assertFalse(any('50.0%' in line for line in output))
            self.assertTrue(any('100.0%' in line for line in output))
            target.set_progress_handler.assert_any_call(None, 0)
            source.close.assert_called_once()
            target.close.assert_called_once()

    def test_environment_persistent_config_and_default_resolve_same_path(self):
        with TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            root = Path(directory)
            self.assertEqual(database_path(root), root / 'db.sqlite3')
            (root / '.local').mkdir()
            (root / '.local/config.json').write_text(json.dumps({'database_path': 'data/planning.sqlite3'}))
            self.assertEqual(database_path(root), root / 'data/planning.sqlite3')
            with patch.dict(os.environ, {'DJANGO_DB_PATH': 'override.sqlite3'}):
                self.assertEqual(database_path(root), root / 'override.sqlite3')
            (root / '.local/config.json').write_text('broken')
            with self.assertRaisesRegex(RuntimeError, 'Не удалось прочитать'):
                database_path(root)

    def test_backup_contains_committed_wal_data_and_leaves_source_unchanged(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'live.sqlite3'
            with sqlite3.connect(source) as database:
                database.execute('PRAGMA journal_mode=WAL')
                database.execute('CREATE TABLE scenario (name TEXT)')
                database.execute("INSERT INTO scenario VALUES ('Plan')")
                database.commit()
                database.execute("INSERT INTO scenario VALUES ('Uncommitted')")
                backup = backup_sqlite(source, root / 'backups')
                with sqlite3.connect(backup) as restored:
                    self.assertEqual(restored.execute('SELECT name FROM scenario').fetchall(), [('Plan',)])
                    self.assertEqual(restored.execute('PRAGMA quick_check').fetchone()[0], 'ok')
                database.rollback()
                self.assertEqual(database.execute('SELECT name FROM scenario').fetchall(), [('Plan',)])
            self.assertNotEqual(backup, backup_sqlite(source, root / 'backups'))
            with self.assertRaises(FileNotFoundError):
                backup_sqlite(root / 'missing.sqlite3', root / 'backups')
            self.assertFalse((root / 'missing.sqlite3').exists())


@override_settings(MS_SQL_CONN_STR='test')
class ScenarioReliabilityTests(TestCase):
    def setUp(self):
        authorize_test_case(self)
        self.scenario = ScenarioModel.objects.create(name='Plan', date_start_plan='2026-01-01', date_end_plan='2026-12-31')
        self.other = ScenarioModel.objects.create(name='Other', date_start_plan='2027-01-01', date_end_plan='2027-12-31')
        kind = KindPurch.objects.create(name='Purchased')
        self.good = PGGoods.objects.create(scenario_plan=self.scenario, planning_group='Group', planning_sales='Sales',
            group_goods='Goods', purch='Supplier', kind_purch=kind, volume=1, exw_usd=10, ddp_usd=15, kddp=1.5,
            stock_cnt_day=30, percent_stock_end=20)
        self.purch = Purch.objects.create(name='Supplier', lag_income=90, lage_make=35)
        self.payment = PurchPay.objects.create(purch=self.purch, name='Payment', percent_pay=100, lag_day_pay=0)

    def record_export(self):
        from .services.copying import copy_goods
        if not PGGoods.objects.filter(scenario_plan=self.other).exists():
            seed = PGGoods.objects.get(pk=self.good.pk)
            seed.pk = None; seed.scenario_plan = self.other; seed.exw_usd = 1
            seed.save()
        copy_goods(self.scenario, self.other)
        other_parameters = snapshot(self.other)
        ScenarioExport.objects.create(scenario=self.other, parameters=other_parameters, fingerprint=fingerprint(other_parameters))
        parameters = snapshot(self.scenario)
        return ScenarioExport.objects.create(scenario=self.scenario, parameters=parameters, fingerprint=fingerprint(parameters))

    def start(self):
        return self.client.post(reverse('start_algorithm_api'), json.dumps({'scope': 'all'}), content_type='application/json')

    def test_selection_persists_across_tabs_without_query_parameter(self):
        self.client.get(reverse('pggoods_list'), {'scenario': self.scenario.pk})
        for url in ['pggoods_list', 'freight', 'results_page']:
            response = self.client.get(reverse(url), follow=True)
            self.assertEqual(response.context['current_scenario'], self.scenario)
            self.assertContains(response, 'id="active-database"')
        self.assertEqual(self.client.get(reverse('pggoods_list'), {'scenario': 'invalid'}).status_code, 404)
        self.scenario.delete()
        response = self.client.get(reverse('pggoods_list'))
        self.assertEqual(response.context['current_scenario'], self.other)

    def test_no_export_and_stale_export_block_start_before_sql(self):
        with patch('model_purch.conns.pyodbc.connect') as connect:
            self.assertEqual(self.start().status_code, 400)
            self.record_export()
            self.good.exw_usd = 11
            self.good.save()
            response = self.start()
            self.assertEqual(response.status_code, 400)
            self.assertIn('Повторите экспорт', response.json()['error'])
            self.assertFalse(AlgorithmRun.objects.exists())
            connect.assert_not_called()

    def test_valid_start_captures_export_and_inputs_then_blocks_concurrent_start(self):
        export = self.record_export()
        response = self.start()
        self.assertEqual(response.status_code, 200)
        run = AlgorithmRun.objects.get(pk=response.json()['run_id'])
        self.assertIsNone(run.scenario_id)
        self.assertIsNone(run.scenario_export_id)
        self.assertEqual(run.parameters['scope'], 'all')
        self.assertEqual(run.parameters['scenarios'][0]['parameters'], export.parameters)
        self.assertEqual({entry['scenario_id'] for entry in run.parameters['scenarios']}, {self.scenario.pk, self.other.pk})
        self.assertEqual(run.steps.count(), 5)
        self.assertEqual(self.start().status_code, 409)
        status = self.client.get(reverse('get_algorithm_statuэ', args=[run.pk])).json()
        self.assertEqual(status['parameters'], run.parameters)
        self.assertTrue(status['parameters']['scenarios'][0]['exported_at'])

    def test_invalid_percent_totals_prices_and_volumes_report_specific_errors(self):
        self.record_export()
        self.payment.percent_pay = 30
        self.payment.save()
        # Simulate invalid legacy database rows without passing through the new pricing validator.
        PGGoods.objects.filter(pk=self.good.pk).update(volume=0, percent_stock_end=150, exw_usd=-1)
        errors = '\n'.join(validation_errors(self.scenario))
        self.assertIn('100%', errors)
        self.assertIn('Объём', errors)
        self.assertIn('EXW', errors)
        self.assertIn('от 0 до 100', errors)
        self.assertEqual(self.start().status_code, 400)

    def test_input_changes_during_run_block_next_step(self):
        self.record_export()
        run_id = self.start().json()['run_id']
        self.good.stock_cnt_day = 50
        self.good.save()
        with patch('model_purch.views._run_algorithm_step') as execute:
            response = self.client.post(reverse('execute_next_step_api', args=[run_id]))
            self.assertEqual(response.status_code, 409)
            execute.assert_not_called()
        self.assertEqual(AlgorithmRun.objects.get(pk=run_id).status, 'failed')

    def test_idle_run_can_be_cancelled_but_running_step_cannot(self):
        self.record_export()
        run_id = self.start().json()['run_id']
        step = AlgorithmStep.objects.filter(algorithm_run_id=run_id).first()
        step.status = 'running'
        step.save()
        url = reverse('cancel_algorithm_api', args=[run_id])
        from .services.algorithm_lock import algorithm_lock
        with algorithm_lock(run_id):
            self.assertEqual(self.client.post(url).status_code, 409)
            self.assertEqual(self.client.post(reverse('execute_next_step_api', args=[run_id])).status_code, 409)
        # Simulates a dead worker: persisted status remains running, OS lock is free.
        self.assertEqual(self.client.post(reverse('execute_next_step_api', args=[run_id])).status_code, 409)
        self.assertEqual(self.client.post(url).json()['status'], 'cancelled')
        step.refresh_from_db()
        self.assertEqual(step.status, 'failed')
        self.assertIsNotNone(step.finished_at)
        self.assertIn('прервано', step.error_message)
        self.assertFalse(AlgorithmStep.objects.filter(algorithm_run_id=run_id, status='pending').exists())
        self.assertEqual(self.client.post(reverse('execute_next_step_api', args=[run_id])).status_code, 409)
        self.assertEqual(self.start().status_code, 200)

    def test_algorithm_requires_csrf_token(self):
        self.record_export()
        client = Client(enforce_csrf_checks=True)
        authorize_admin(client)
        self.assertEqual(client.post(reverse('start_algorithm_api'), {'scenario': self.scenario.pk}).status_code, 403)
        client.get(reverse('results_page'), {'scenario': self.scenario.pk})
        response = client.post(reverse('start_algorithm_api'), {'scenario': self.scenario.pk},
                               HTTP_X_CSRFTOKEN=client.cookies['csrftoken'].value)
        self.assertEqual(response.status_code, 200)

    @patch('model_purch.views.MS_SQL_CONN_STR', 'test')
    @patch('model_purch.views.pyodbc.connect')
    def test_only_successful_sql_export_records_snapshot(self, connect):
        connection = Mock()
        cursor = connection.cursor.return_value
        cursor.description = [('id',), ('planning_group',), ('scenario_name',)]
        cursor.fetchall.return_value = []
        cursor.fetchone.side_effect = [(501,), (701,)]
        connect.return_value = connection
        self.client.post(reverse('export_scenario_to_sql', args=[self.scenario.pk]))
        export = self.scenario.exports.get()
        self.assertEqual(export.parameters, snapshot(self.scenario))
        connection.rollback.assert_not_called()
        cursor.execute.side_effect = RuntimeError('SQL export failed')
        self.client.post(reverse('export_scenario_to_sql', args=[self.scenario.pk]))
        self.assertEqual(self.scenario.exports.count(), 1)
        connection.rollback.assert_called_once()

    def test_legacy_unfinished_run_is_visible_and_can_be_cancelled(self):
        legacy = AlgorithmRun.objects.create(status='running')
        AlgorithmStep.objects.create(algorithm_run=legacy, order=2, name='Prepare', status='pending')
        response = self.client.get(reverse('results_page'), {'scenario': self.scenario.pk})
        self.assertContains(response, 'старый запуск без сценария')
        self.assertContains(response, f'cancelRun({legacy.pk})')
        self.assertEqual(self.client.post(reverse('cancel_algorithm_api', args=[legacy.pk])).json()['status'], 'cancelled')

    @patch('model_purch.views.MS_SQL_CONN_STR', 'test')
    def test_calculation_page_prepares_only_stale_data_and_hides_step_when_current(self):
        self.record_export()
        PGGoods.objects.filter(pk=self.good.pk).update(exw_usd=12)
        page = self.client.get(reverse('results_page'))
        self.assertEqual([item.pk for item in page.context['pending_transfers']],[self.scenario.pk])
        self.assertContains(page,'prepare-calculation-data-form')
        def transfer(request, scenario):
            params = snapshot(scenario)
            ScenarioExport.objects.create(scenario=scenario,parameters=params,fingerprint=fingerprint(params))
            return True
        with patch('model_purch.views._export_scenario_to_sql',side_effect=transfer) as export:
            response = self.client.post(reverse('prepare_calculation_data'))
        self.assertEqual(response.url,reverse('results_page'))
        self.assertEqual([call.args[1].pk for call in export.call_args_list],[self.scenario.pk])
        self.assertFalse(AlgorithmRun.objects.exists())
        page = self.client.get(reverse('results_page'))
        self.assertNotContains(page,'id="prepare-calculation-data-form"')
        self.assertEqual(page.context['readiness_errors'],[])

    @patch('model_purch.views.MS_SQL_CONN_STR', 'test')
    def test_prepare_data_skips_current_and_blocks_invalid_or_active_runs(self):
        self.record_export()
        url = reverse('prepare_calculation_data')
        with patch('model_purch.views._export_scenario_to_sql') as export:
            self.client.post(url)
            export.assert_not_called()
            PGGoods.objects.filter(pk=self.good.pk).update(exw_usd=12)
            active = AlgorithmRun.objects.create(status='running')
            self.client.post(url)
            export.assert_not_called()
            active.delete()
            PGGoods.objects.filter(pk=self.good.pk).update(exw_usd=0)
            self.client.post(url)
            export.assert_not_called()
        self.assertFalse(AlgorithmRun.objects.exists())
        self.assertEqual(self.client.get(url).status_code,405)

    def test_prepare_data_requires_csrf_and_is_not_available_to_analyst(self):
        from django.contrib.auth.models import User
        from base.models import UserProjectAccess
        url = reverse('prepare_calculation_data')
        csrf = Client(enforce_csrf_checks=True)
        csrf.force_login(self.client.session and User.objects.get(pk=self.client.session['_auth_user_id']))
        self.assertEqual(csrf.post(url).status_code,403)
        analyst = User.objects.create_user('prepare-readonly')
        UserProjectAccess.objects.create(user=analyst,project='purchases',role='Аналитик')
        self.client.force_login(analyst)
        self.assertEqual(self.client.post(url).status_code,403)

    @patch('model_purch.views.MS_SQL_CONN_STR', 'test')
    def test_partial_preparation_does_not_allow_calculation_and_retry_keeps_only_failed(self):
        self.record_export()
        ScenarioExport.objects.all().delete()
        def transfer(request, scenario):
            if scenario.pk == self.scenario.pk:
                return False
            params = snapshot(scenario)
            ScenarioExport.objects.create(scenario=scenario,parameters=params,fingerprint=fingerprint(params))
            return True
        with patch('model_purch.views._export_scenario_to_sql',side_effect=transfer) as export:
            self.client.post(reverse('prepare_calculation_data'))
        self.assertEqual(export.call_count,2)
        page = self.client.get(reverse('results_page'))
        self.assertEqual([item.pk for item in page.context['pending_transfers']],[self.scenario.pk])
        self.assertEqual(self.start().status_code,400)
        self.assertFalse(AlgorithmRun.objects.exists())
