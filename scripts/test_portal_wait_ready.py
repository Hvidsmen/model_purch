import tempfile
from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

from scripts.portal_wait_ready import wait_ready


class ReadinessTests(unittest.TestCase):
    def test_waits_through_connection_failure_and_server_error(self):
        response = MagicMock()
        response.__enter__.return_value.status = 200
        opener = MagicMock()
        opener.open.side_effect = [URLError('not listening'), HTTPError('http://localhost', 500, 'error', {}, None), response]
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / 'log'
            log.write_text('Backup copy: 50%')
            with patch('scripts.portal_wait_ready.build_opener', return_value=opener), \
                    patch('scripts.portal_wait_ready.time.sleep'), \
                    patch('builtins.print') as output:
                self.assertEqual(wait_ready('0.0.0.0', 8000, log), 0)
                self.assertEqual(opener.open.call_count, 3)
                opener.open.assert_called_with('http://127.0.0.1:8000/accounts/login/', timeout=3)
                self.assertTrue(any('PORTAL READY' in str(call) for call in output.call_args_list))

    def test_timeout_does_not_claim_ready(self):
        with patch('scripts.portal_wait_ready.time.monotonic', side_effect=[0, 1801]), \
                patch('scripts.portal_wait_ready.build_opener') as opener, \
                patch('builtins.print') as output:
            self.assertEqual(wait_ready('::', 8000, '/missing'), 1)
            opener.return_value.open.assert_not_called()
            self.assertFalse(any('PORTAL READY' in str(call) for call in output.call_args_list))
