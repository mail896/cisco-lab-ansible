"""Regression checks for the explicitly enabled evening policy and stale mount handling."""
from contextlib import nullcontext
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from maintenance import pilot
from maintenance.preview import STOCK_HASH


class EveningTests(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.enterContext(patch.object(pilot, 'read', return_value=''))
        self.enterContext(patch.object(pilot, 'apt', side_effect=lambda *args: self.events.append(('apt', args))))
        self.enterContext(patch.object(pilot, 'login_gate', side_effect=nullcontext))
        self.enterContext(patch.object(pilot, 'occupied', return_value=False))
        self.enterContext(patch.object(pilot, 'MODULE', SimpleNamespace(read_bytes=lambda: b'stock')))
        self.enterContext(patch.object(pilot.hashlib, 'sha256', return_value=SimpleNamespace(hexdigest=lambda: STOCK_HASH)))
        self.enterContext(patch.object(pilot, 'simulated', return_value={'install': [], 'remove': []}))
        self.enterContext(patch.object(pilot, 'current_bootable', return_value=[]))
        self.enterContext(patch.object(pilot, 'log'))
        self.enterContext(patch.object(pilot, 'write_result'))
        self.logout = self.enterContext(patch.object(pilot, 'finish_sessions', side_effect=lambda: self.events.append(('logout',))))
        self.unmount = self.enterContext(patch.object(pilot, 'unmount_stale_homes', side_effect=lambda: self.events.append(('unmount',))))
        self.run = self.enterContext(patch.object(pilot.subprocess, 'run', side_effect=lambda *a, **kw: self.events.append(('run', a[0]))))

    def test_success_restarts_even_without_update_requirement(self):
        self.assertTrue(pilot.maintain(evening_restart=True))
        self.assertEqual([event[0] for event in self.events], ['apt', 'logout', 'unmount', 'run'])
        self.assertEqual(self.events[-1][1], ['systemctl', '--no-block', 'reboot'])

    def test_incomplete_logout_leaves_machine_running(self):
        self.logout.side_effect = RuntimeError('logout blocked')
        with self.assertRaises(RuntimeError):
            pilot.maintain(evening_restart=True)
        self.unmount.assert_not_called()
        self.run.assert_not_called()

    def test_failed_package_audit_never_restarts(self):
        with patch.object(pilot, 'read', return_value='dpkg incomplete'):
            with self.assertRaises(RuntimeError):
                pilot.maintain(evening_restart=True)
        self.logout.assert_not_called()
        self.run.assert_not_called()

    def test_original_policy_still_downloads_only_for_occupied_client(self):
        with patch.object(pilot, 'occupied', return_value=True):
            self.assertFalse(pilot.maintain())
        self.logout.assert_not_called()
        self.run.assert_not_called()
        self.assertIn('--download-only', self.events[-1][1])


class StaleHomeTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(TemporaryDirectory()))
        self.home = self.root / 'student'
        self.home.mkdir()
        self.enterContext(patch.object(pilot, 'HOME_ROOT', self.root))
        self.owner = SimpleNamespace(pw_name='student', pw_uid=os.getuid(), pw_dir=str(self.home))
        self.enterContext(patch.object(pilot.pwd, 'getpwnam', return_value=self.owner))
        original = Path.read_text
        self.enterContext(patch.object(Path, 'read_text', lambda path, *a, **kw:
                          'root:x:0:0:root:/root:/bin/bash' if str(path) == '/etc/passwd' else
                          'SYNC=0\n' if str(path) == '/etc/iserv/config' else original(path, *a, **kw)))
        self.enterContext(patch.object(pilot, 'has_user_processes', return_value=False))
        self.enterContext(patch.object(pilot, 'log'))
        self.run = self.enterContext(patch.object(pilot.subprocess, 'run'))
        self.table = [{'target': str(self.home), 'fstype': 'tmpfs'}]
        self.enterContext(patch.object(pilot, 'read', side_effect=lambda *args: json.dumps({'filesystems': self.table})))

    def test_only_normal_unmount_of_validated_idle_ram_home(self):
        marker = self.home / 'marker.txt'
        marker.touch()
        pilot.unmount_stale_homes()
        self.run.assert_called_once_with(['umount', '--', str(self.home)], check=True, timeout=30)
        self.assertTrue(marker.exists())

    def test_live_process_or_unknown_submount_prevents_unmount(self):
        with patch.object(pilot, 'has_user_processes', return_value=True):
            with self.assertRaises(RuntimeError):
                pilot.unmount_stale_homes()
        self.table.append({'target':str(self.home / 'Unexpected'), 'fstype':'cifs'})
        with self.assertRaises(RuntimeError):
            pilot.unmount_stale_homes()
        self.run.assert_not_called()

    def test_known_shares_unmount_before_home(self):
        self.table.append({'target':str(self.home / 'Files'), 'fstype':'cifs'})
        pilot.unmount_stale_homes()
        self.assertEqual([call.args[0] for call in self.run.call_args_list], [
            ['umount', '--', str(self.home / 'Files')], ['umount', '--', str(self.home)]])
