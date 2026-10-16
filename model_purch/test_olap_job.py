from unittest.mock import Mock, patch
from django.test import SimpleTestCase, override_settings
from .services.olap_job import process_cube, JOB_RESULT


class OlapJobTests(SimpleTestCase):
    def setup_connection(self, rows):
        connection, cursor = Mock(), Mock()
        cursor.fetchone.side_effect = rows
        cursor.nextset.return_value = False
        connector = Mock(return_value=(connection, cursor))
        return connector, connection, cursor

    def test_history_query_ignores_old_success_and_individual_step_results(self):
        import sqlite3
        db = sqlite3.connect(':memory:')
        self.addCleanup(db.close)
        db.execute('CREATE TABLE history(job_id, step_id, instance_id, run_status, message)')
        db.executemany('INSERT INTO history VALUES (?, ?, ?, ?, ?)', [
            ('job', 0, 100, 1, 'old success'), ('other', 0, 101, 1, 'different job'),
            ('job', 1, 102, 1, 'step success'), ('job', 0, 103, 4, 'in progress'),
            ('job', 0, 104, 0, 'new failure')])
        query = JOB_RESULT.replace('TOP (1) ', '').replace('msdb.dbo.sysjobhistory', 'history') + ' LIMIT 1'
        self.assertEqual(db.execute(query, ('job', 100)).fetchone(), (0, 'new failure'))
        self.assertIsNone(db.execute(query, ('job', 104)).fetchone())

    def test_waits_for_new_completed_job_not_old_history_or_initial_absence(self):
        connector, connection, cursor = self.setup_connection([
            ('job-id', 7, 123, 0), (7,), None, (7,), (1, 'succeeded')])
        with patch('model_purch.services.olap_job.time.sleep') as wait:
            result = process_cube(connector)
        wait.assert_called_once_with(5)
        self.assertIn('успешно завершено', result['message'])
        result_calls = [c for c in cursor.execute.call_args_list if c.args[0] == JOB_RESULT]
        self.assertEqual([c.args[1:] for c in result_calls], [('job-id', 123)] * 2)
        self.assertIn('step_id = 0 AND instance_id > ?', JOB_RESULT)
        connection.close.assert_called_once()

    def test_job_failure_and_cancellation_are_not_success(self):
        for status in (0, 3):
            connector, connection, _ = self.setup_connection([('job-id', 7, 123, 0), (7,), (status, 'Agent detail')])
            with self.subTest(status=status), self.assertRaisesRegex(RuntimeError, 'Agent detail'):
                process_cube(connector)
            connection.close.assert_called_once()

    def test_busy_missing_job_and_agent_restart_never_report_success(self):
        for rows, message in [([None], 'не найдено'), ([('job-id', 7, 123, 1)], 'уже выполняется'),
                              ([('job-id', 7, 123, 0), (8,)], 'перезапущен')]:
            connector, connection, cursor = self.setup_connection(rows)
            with self.subTest(message=message), self.assertRaisesRegex(RuntimeError, message):
                process_cube(connector)
            if message != 'перезапущен':
                self.assertFalse(any('sp_start_job' in c.args[0] for c in cursor.execute.call_args_list))
            connection.close.assert_called_once()

    @override_settings(OLAP_JOB_TIMEOUT_SECONDS=10)
    def test_timeout_leaves_server_job_running(self):
        connector, connection, cursor = self.setup_connection([('job-id', 7, 123, 0), (7,), None])
        with patch('model_purch.services.olap_job.time.monotonic', side_effect=[0, 11]), patch('model_purch.services.olap_job.time.sleep') as wait:
            with self.assertRaisesRegex(RuntimeError, 'не остановлено'):
                process_cube(connector)
        wait.assert_not_called()
        self.assertFalse(any('sp_stop_job' in c.args[0] for c in cursor.execute.call_args_list))
        connection.close.assert_called_once()

    def test_failed_start_closes_connection_without_polling_or_wait(self):
        connector, connection, cursor = self.setup_connection([('job-id', 7, 123, 0)])
        def execute(sql, *args):
            if 'sp_start_job' in sql:
                raise RuntimeError('job start failed')
        cursor.execute.side_effect = execute
        with patch('model_purch.services.olap_job.time.sleep') as wait, self.assertRaisesRegex(RuntimeError, 'job start failed'):
            process_cube(connector)
        wait.assert_not_called()
        connection.close.assert_called_once()
