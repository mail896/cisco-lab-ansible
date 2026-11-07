import contextlib
import io
import subprocess
import unittest
from unittest.mock import patch

from maintenance import teacher_wake_test as wake


class WakeTests(unittest.TestCase):
    def test_help_or_missing_test_flag_never_triggers_suspend(self):
        for arguments, code in ((['test', '--help'], 0), (['test'], 2)):
            with (patch('sys.argv', arguments), patch.object(wake, 'cycle') as cycle,
                  contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO())):
                with self.assertRaises(SystemExit) as result:
                    wake.main()
                self.assertEqual(result.exception.code, code)
                cycle.assert_not_called()

    def test_running_pc_or_early_keyboard_wake_cannot_pass(self):
        start = dict(boot=1000, monotonic=1000)
        self.assertFalse(wake.classify(start, dict(boot=1120, monotonic=1120), True)['passed'])
        self.assertFalse(wake.classify(start, dict(boot=1015, monotonic=1005), True)['passed'])

    def test_actual_suspend_requires_a_fired_timer(self):
        start = dict(boot=1000, monotonic=1000)
        end = dict(boot=1121, monotonic=1002)
        self.assertTrue(wake.classify(start, end, True)['passed'])
        self.assertFalse(wake.classify(start, end, False)['passed'])

    def test_unavailable_wake_timer_never_suspends(self):
        def read(*args):
            return 'not-found' if args[-2] == 'LoadState' else 'inactive'
        with patch.object(wake, 'read', side_effect=read), patch.object(wake.subprocess, 'run') as run:
            with self.assertRaisesRegex(RuntimeError, 'nicht aktiv'):
                wake.cycle()
        self.assertNotIn(['systemctl', 'suspend'], [call.args[0] for call in run.call_args_list])
        self.assertEqual(run.call_args_list[-1].args[0][:2], ['systemctl', 'stop'])

    def test_failure_to_create_timer_never_suspends_or_stops_uncreated_units(self):
        def read(*args):
            return 'not-found' if args[-2] == 'LoadState' else 'inactive'
        with patch.object(wake, 'read', side_effect=read), patch.object(wake.subprocess, 'run') as run:
            run.side_effect = subprocess.CalledProcessError(1, ['systemd-run'])
            with self.assertRaises(subprocess.CalledProcessError):
                wake.cycle()
        self.assertEqual(run.call_count, 1)
