"""Retired teacher suspend entry points must never issue system commands."""
import contextlib
import io
import unittest
from unittest.mock import patch

from maintenance import teacher_wake_test as wake


class RetiredWakeTests(unittest.TestCase):
    def test_old_command_or_default_invocation_cannot_suspend(self):
        for arguments in (['test'], ['test', '--suspend-test']):
            with (patch('sys.argv', arguments), patch('subprocess.run') as run,
                  contextlib.redirect_stderr(io.StringIO())):
                with self.assertRaises(SystemExit) as result:
                    wake.main()
                self.assertEqual(result.exception.code, 1)
                run.assert_not_called()

    def test_imported_test_cycle_also_refuses(self):
        with patch('subprocess.run') as run:
            with self.assertRaisesRegex(RuntimeError, 'dauerhaft gesperrt'):
                wake.cycle()
            run.assert_not_called()
