import subprocess
import sys
import tempfile
from django.test import SimpleTestCase, override_settings
from .services.algorithm_lock import algorithm_lock, AlgorithmBusy


class AlgorithmLockTests(SimpleTestCase):
    def test_process_death_releases_lock_even_when_file_remains(self):
        with tempfile.TemporaryDirectory() as root, override_settings(ALGORITHM_LOCK_DIR=root):
            code = '''
import sys, time
from django.conf import settings
settings.configure(BASE_DIR=sys.argv[1], ALGORITHM_LOCK_DIR=sys.argv[1])
from model_purch.services.algorithm_lock import algorithm_lock
with algorithm_lock(42):
    print('locked', flush=True)
    time.sleep(30)
'''
            worker = subprocess.Popen([sys.executable, '-c', code, root], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                self.assertEqual(worker.stdout.readline().strip(), 'locked')
                with self.assertRaises(AlgorithmBusy):
                    with algorithm_lock(42):
                        self.fail('A second worker acquired a live lock')
            finally:
                worker.kill()
                worker.communicate(timeout=10)
            with algorithm_lock(42):
                pass  # A crash leaves a file, but does not leave an active lock.
