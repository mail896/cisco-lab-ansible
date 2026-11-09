"""Bounded fixed-target reachability; no LAB packets from tests."""
import threading
import unittest
from unittest.mock import Mock, patch

from dashboard import presence


class PresenceTests(unittest.TestCase):
    def test_fixed_targets_and_ping_packet_match(self):
        self.assertEqual(len(presence.TARGETS), 17)
        self.assertEqual(presence.TARGETS['LAB-Teacher'], '203.0.113.100')
        sock = Mock()
        sock.__enter__ = Mock(return_value=sock); sock.__exit__ = Mock(return_value=False)
        def reply(_):
            packet = sock.sendto.call_args.args[0]
            self.assertEqual(presence.checksum(packet), 0)
            return b'\0\0' + packet[2:], ('203.0.113.15', 0)
        sock.recvfrom.side_effect = reply
        with patch.object(presence.socket, 'socket', return_value=sock):
            row = presence.ping('203.0.113.15')
        self.assertEqual(row['state'], 'online')
        self.assertGreaterEqual(row['rtt_ms'], 0)
        with self.assertRaises(ValueError): presence.ping('203.0.113.254')

    def test_timeout_is_no_response_and_permission_error_is_unknown(self):
        sock = Mock(); sock.__enter__ = Mock(return_value=sock); sock.__exit__ = Mock(return_value=False)
        sock.recvfrom.side_effect = TimeoutError
        with patch.object(presence.socket, 'socket', return_value=sock):
            self.assertEqual(presence.ping('203.0.113.15')['state'], 'unreachable')
        with patch.object(presence.socket, 'socket', side_effect=PermissionError):
            self.assertEqual(presence.ping('203.0.113.15')['state'], 'unknown')

    def test_clients_share_one_bounded_batch_and_cache(self):
        release, started, finished = threading.Event(), threading.Event(), threading.Event()
        calls = []; lock = threading.Lock(); now = [100.0]
        def probe(address):
            with lock: calls.append(address)
            started.set(); release.wait(2)
            return dict(state='online', rtt_ms=1.0)
        monitor = presence.Presence(probe=probe, clock=lambda:now[0])
        original = monitor._refresh
        def refresh():
            try: original()
            finally: finished.set()
        self.addCleanup(release.set)
        with patch.object(monitor, '_refresh', side_effect=refresh):
            self.assertTrue(monitor.snapshot()['refreshing']); self.assertTrue(started.wait(1))
            for _ in range(50): self.assertTrue(monitor.snapshot()['refreshing'])
            self.assertLessEqual(len(calls),4)
            release.set(); self.assertTrue(finished.wait(2))
            for _ in range(50): self.assertFalse(monitor.snapshot()['refreshing'])
            self.assertEqual(len(calls),17)
            now[0]+=29; self.assertFalse(monitor.snapshot()['refreshing'])
            finished.clear();now[0]+=1;monitor.snapshot();self.assertTrue(finished.wait(2))
            self.assertEqual(len(calls),34)
        snapshot=monitor.snapshot();snapshot['devices']['LAB-08A']['state']='corrupted'
        self.assertEqual(monitor.snapshot()['devices']['LAB-08A']['state'],'online')

    def test_probe_error_does_not_leave_monitor_stuck(self):
        monitor=presence.Presence(probe=Mock(side_effect=OSError))
        monitor.refreshing=True;monitor._refresh()
        self.assertFalse(monitor.refreshing)
        self.assertTrue(all(row['state']=='unknown' for row in monitor.devices.values()))


if __name__=='__main__':unittest.main()
