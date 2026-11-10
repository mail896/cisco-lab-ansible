"""Diagnostic keys cannot become arbitrary SSH/root command execution."""
import contextlib
import io
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from deployment.teacher import diagnostics, install


class DiagnosticTests(unittest.TestCase):
    def test_remote_input_cannot_add_commands_or_arguments(self):
        for value in ('', 'previous;id', 'previous\n', 'previous -n 99999', 'bash',
                      'cat /etc/shadow', 'services && reboot', 'x' * 201):
            with self.subTest(value=value), patch.object(diagnostics.sys, 'argv', ['diagnostics-ssh']), \
                    patch.dict(diagnostics.os.environ, {'SSH_ORIGINAL_COMMAND': value}), \
                    patch.object(diagnostics.subprocess, 'run') as run:
                with self.assertRaises(ValueError):
                    diagnostics.main()
                run.assert_not_called()

    def test_transport_does_not_forward_a_shell_or_arguments(self):
        with patch.object(diagnostics.sys, 'argv', ['diagnostics-ssh']), \
                patch.dict(diagnostics.os.environ, {'SSH_ORIGINAL_COMMAND': 'previous'}), \
                patch.object(diagnostics.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0)) as run:
            with self.assertRaises(SystemExit) as ended:
                diagnostics.main()
            self.assertEqual(ended.exception.code, 0)
            self.assertEqual(run.call_args.args[0], ['/usr/bin/sudo', '-n', diagnostics.ENDPOINT])
            self.assertEqual(run.call_args.kwargs['input'], 'previous')

    def test_previous_boot_is_read_with_bounded_output_and_timeout(self):
        result = subprocess.CompletedProcess([], 0, stdout='previous boot\n')
        with patch.object(diagnostics.sys, 'argv', ['diagnostics']), \
                patch.object(diagnostics.os, 'geteuid', return_value=0), \
                patch.object(diagnostics.socket, 'gethostname', return_value='LAB-Teacher'), \
                patch.object(diagnostics.sys, 'stdin', io.StringIO('previous')), \
                patch.object(diagnostics.subprocess, 'run', return_value=result) as run, \
                contextlib.redirect_stdout(io.StringIO()) as out:
            diagnostics.main()
            self.assertIn('previous boot', out.getvalue())
            self.assertEqual(run.call_args.args[0][-2:], ['-b', '-1'])
            self.assertEqual(run.call_args.kwargs['timeout'], 20)
            self.assertEqual(run.call_args.kwargs['stdin'], subprocess.DEVNULL)

    def test_receiver_update_preserves_separate_root_managed_key(self):
        with TemporaryDirectory() as directory:
            state = Path(directory)
            (state / 'diagnostic-key.pub').write_text('ssh-ed25519 AAAADIAG\n')
            with patch.object(install, 'STATE', state), patch.object(install, 'regular') as checked:
                files = install.files('ssh-ed25519 AAAASERVICE',
                                      dict(receiver='', preview='', software_client='', deb_client=''))
            checked.assert_called_once_with(state / 'diagnostic-key.pub')
            keys = files[install.HOME / '.ssh/authorized_keys'][0].decode().splitlines()
            self.assertEqual(len(keys), 2)
            self.assertIn('cisco-lab-teacher-ssh', keys[0])
            self.assertIn('cisco-lab-teacher-diagnostics-ssh', keys[1])
            self.assertTrue(keys[1].endswith('ssh-ed25519 AAAADIAG'))


if __name__ == '__main__':
    unittest.main()
