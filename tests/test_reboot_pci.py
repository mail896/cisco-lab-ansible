"""Preserve image boot options and report the actual live reset mode."""
import contextlib
import io
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import Mock, patch

import yaml


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / 'files/cisco-lab-reboot.cfg'
TASKS = yaml.safe_load((ROOT / 'tasks/reboot-pci.yml').read_text())
RUNTIME = TASKS[-1]['ansible.builtin.command']['argv'][-1]


class RebootPCITests(unittest.TestCase):
    def test_dropin_preserves_existing_options_and_can_be_sourced_twice(self):
        cases = ('', 'quiet', 'quiet splash console=ttyS0,115200n8',
                 'quiet reboot=pci', 'quiet parameter="two words"')
        script = '''
GRUB_CMDLINE_LINUX_DEFAULT=$1
GRUB_CMDLINE_LINUX='splash existing=keep'
. "$2"
. "$2"
printf '%s\n%s\n' "$GRUB_CMDLINE_LINUX_DEFAULT" "$GRUB_CMDLINE_LINUX"
'''
        for initial in cases:
            with self.subTest(initial=initial):
                result = subprocess.check_output(
                    ['/bin/sh', '-eu', '-c', script, 'test', initial, str(CONFIG)], text=True)
                default, common = result.splitlines()
                expected = initial if 'reboot=pci' in initial.split() else (initial + ' reboot=pci').strip()
                self.assertEqual(default, expected)
                self.assertEqual(default.split().count('reboot=pci'), 1)
                self.assertEqual(common, 'splash existing=keep')

    def test_live_switch_changes_acpi_once_and_accepts_existing_pci(self):
        for initial in ('acpi', 'pci'):
            with self.subTest(initial=initial):
                reset = Mock()
                reset.read_text.side_effect = [initial + '\n', 'pci\n']
                output = io.StringIO()
                with patch('pathlib.Path', return_value=reset) as path, contextlib.redirect_stdout(output):
                    exec(compile(RUNTIME, '<runtime-pci>', 'exec'), {})
                path.assert_called_once_with('/sys/kernel/reboot/type')
                if initial == 'pci':
                    reset.write_text.assert_not_called()
                else:
                    reset.write_text.assert_called_once_with('pci\n')
                self.assertEqual(json.loads(output.getvalue()),
                                 {'changed': initial != 'pci', 'before': initial, 'type': 'pci'})

    def test_kernel_refusal_is_not_reported_as_success(self):
        reset = Mock()
        reset.read_text.return_value = 'acpi\n'
        output = io.StringIO()
        with patch('pathlib.Path', return_value=reset), contextlib.redirect_stdout(output):
            with self.assertRaises(SystemExit):
                exec(compile(RUNTIME, '<runtime-pci>', 'exec'), {})
        self.assertEqual(output.getvalue(), '')


if __name__ == '__main__':
    unittest.main()
