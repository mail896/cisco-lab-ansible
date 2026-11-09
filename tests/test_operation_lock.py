from pathlib import Path
from tempfile import TemporaryDirectory
import subprocess
import sys
import unittest
from unittest.mock import patch
from dashboard.operation_lock import OperationLock


class OperationLockTests(unittest.TestCase):
    def test_status_never_acquires_or_releases_an_operation_lock(self):
        with TemporaryDirectory() as directory:
            operation = OperationLock(Path(directory) / 'operation.lock')
            try:
                with patch.object(operation, 'acquire', side_effect=AssertionError('Status took a lock')), \
                     patch.object(operation, 'release', side_effect=AssertionError('Status released a lock')):
                    self.assertFalse(operation.locked())
            finally:
                operation.file.close()

    def test_status_observes_a_real_separate_process_without_disturbing_it(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'operation.lock'
            operation = OperationLock(path)
            worker = subprocess.Popen([sys.executable, '-B', '-c',
                'import fcntl, sys; f=open(sys.argv[1], "r+b"); '
                'fcntl.flock(f, fcntl.LOCK_EX); print("locked", flush=True); sys.stdin.read(1)', str(path)],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
            try:
                self.assertEqual(worker.stdout.readline().strip(), 'locked')
                self.assertTrue(operation.locked())
                self.assertFalse(operation.acquire(blocking=False))
            finally:
                worker.communicate('x', timeout=3)
            try:
                self.assertFalse(operation.locked())
                self.assertTrue(operation.acquire(blocking=False))
                operation.release()
            finally:
                operation.file.close()

    def test_separate_instances_observe_and_release_the_same_operation(self):
        with TemporaryDirectory() as directory:
            first=OperationLock(Path(directory)/'operation.lock')
            second=OperationLock(Path(directory)/'operation.lock')
            self.assertTrue(first.acquire(blocking=False))
            try:
                self.assertTrue(second.locked())
                self.assertFalse(second.acquire(blocking=False))
            finally:
                first.release()
            self.assertFalse(second.locked())
            self.assertTrue(second.acquire(blocking=False))
            second.release()
            first.file.close();second.file.close()
