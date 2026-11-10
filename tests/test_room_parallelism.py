"""All selected student clients can begin an action before any one finishes."""
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
from types import SimpleNamespace
import unittest

from dashboard.room import RoomActions
from dashboard.targets import LAB_TARGETS


class RoomParallelismTests(unittest.TestCase):
    def test_all_sixteen_logins_and_power_actions_can_start_together(self):
        for action in ('demo', 'poweroff'):
            with self.subTest(action=action), TemporaryDirectory() as directory:
                lock = threading.Lock(); lock.acquire()
                room = RoomActions(Path(directory), SimpleNamespace(lock=lock))
                ready = threading.Barrier(16, timeout=3)
                started = set()
                guard = threading.Lock()

                def on_host(host, _action, _wake):
                    with guard:
                        started.add(host)
                    ready.wait()
                    return 'Bestätigt.'

                room.on_host = on_host
                run = dict(id='example-run', action=action, targets=list(LAB_TARGETS),
                           status='running', results={})
                room.execute(run, None)
                self.assertEqual(started, set(LAB_TARGETS))
                self.assertEqual(run['status'], 'finished')
                self.assertEqual(len(run['results']), 16)
                self.assertFalse(lock.locked())


if __name__ == '__main__':
    unittest.main()
