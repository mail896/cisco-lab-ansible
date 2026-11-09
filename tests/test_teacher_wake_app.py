"""APP Teacher-WOL preparation must reject wrong targets and never sleep the Teacher."""
import base64
import hashlib
import subprocess
import unittest
from unittest.mock import patch

from maintenance import teacher_wake


class TeacherWakeTests(unittest.TestCase):
    def setUp(self):
        self.data = dict(version=1, host='LAB-Teacher', address='203.0.113.100',
                         school_mac='02:11:22:33:44:55', fingerprint='SHA256:' + 'a' * 43)
        self.enterContext(patch.object(teacher_wake.socket, 'gethostname', return_value='apps'))
        self.enterContext(patch.object(teacher_wake, 'read_settings', return_value=self.data))
        self.enterContext(patch.object(teacher_wake.RoomActions, 'wake_route',
                                      return_value=('203.0.113.200', '203.0.113.255')))
        self.packet = self.enterContext(patch.object(teacher_wake.RoomActions, 'wake'))

    def test_preview_and_already_online_never_send_packet(self):
        with patch.object(teacher_wake, 'ssh_identity', return_value=False):
            teacher_wake.run(wake=False)
        with patch.object(teacher_wake, 'ssh_identity', return_value=True):
            teacher_wake.run(wake=True)
        self.packet.assert_not_called()

    def test_wake_sends_only_to_fixed_mac_then_requires_matching_ssh_identity(self):
        with (patch.object(teacher_wake, 'ssh_identity', side_effect=[False, True]) as probe,
              patch.object(teacher_wake.time, 'sleep'),
              patch.object(teacher_wake.time, 'monotonic', return_value=0)):
            teacher_wake.run(wake=True)
        self.packet.assert_called_once_with(self.data['school_mac'],
                                            ('203.0.113.200', '203.0.113.255'))
        self.assertEqual(probe.call_count, 2)

    def test_target_mac_and_route_mismatch_stop_before_packet(self):
        for invalid in ('ff:ff:ff:ff:ff:ff', '00:00:00:00:00:00'):
            self.data['school_mac'] = invalid
            with self.assertRaises(ValueError):
                teacher_wake.run(wake=True)
        self.data['school_mac'] = '02:11:22:33:44:55'
        self.data['address'] = '203.0.113.101'
        with self.assertRaises(ValueError):
            teacher_wake.run(wake=True)
        self.data['address'] = '203.0.113.100'
        with patch.object(teacher_wake.RoomActions, 'wake_route', return_value=('203.0.113.200', '203.0.113.253')):
            with self.assertRaises(ValueError):
                teacher_wake.run(wake=True)
        self.packet.assert_not_called()

    def test_ssh_keyscan_must_match_independently_confirmed_fingerprint(self):
        blob = b'test-host-key'
        key = base64.b64encode(blob).decode()
        fingerprint = 'SHA256:' + base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip('=')
        result = subprocess.CompletedProcess([], 0, f'203.0.113.100 ssh-ed25519 {key}\n', '')
        with patch.object(teacher_wake.subprocess, 'run', return_value=result):
            self.assertTrue(teacher_wake.ssh_identity(fingerprint))
            with self.assertRaisesRegex(ValueError, 'Fingerabdruck'):
                teacher_wake.ssh_identity(self.data['fingerprint'])


if __name__ == '__main__':
    unittest.main()
