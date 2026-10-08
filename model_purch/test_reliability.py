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
        self.payment.percent_pay = 30
        self.payment.save()
        self.good.volume = 0
        self.good.percent_stock_end = 150
        self.good.exw_usd = -1
        self.good.save()
        errors = '\n'.join(validation_errors(self.scenario))
        self.assertIn('100%', errors)
        self.assertIn('Объём', errors)
        self.assertIn('EXW', errors)
        self.assertIn('от 0 до 100', errors)
        self.record_export()
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
        self.assertEqual(self.client.post(url).status_code, 409)
        step.status = 'pending'
        step.save()
        self.assertEqual(self.client.post(url).json()['status'], 'cancelled')
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
