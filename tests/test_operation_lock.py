from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from dashboard.operation_lock import OperationLock


class OperationLockTests(unittest.TestCase):
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
