"""Orchestration must prove a reboot and preserve failed or occupied clients."""
from datetime import datetime, timedelta
import subprocess
import unittest
from unittest.mock import patch

from maintenance import controller
from maintenance.preview import BERLIN

start_service = controller.start_service


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.host = 'LAB-08A'
        self.record = {'mac': '88:ae:dd:0a:ac:22', 'known_hosts': '/private/pin'}
        self.enterContext(patch.object(controller.time, 'sleep'))
        self.reachable = self.enterContext(patch.object(controller, 'reachable', return_value=True))
        self.probe = self.enterContext(patch.object(controller, 'probe'))
        self.start = self.enterContext(patch.object(controller, 'start_service'))
        self.power = self.enterContext(patch.object(controller.RoomActions, 'on_host'))
        self.wake = self.enterContext(patch.object(controller.RoomActions, 'wake'))
        self.enterContext(patch.object(controller.RoomActions, '__init__', return_value=None))

    def state(self, **values):
        data = dict(evening=True, active='inactive', uptime=10000,
                    boot_id='old', occupied=False, home_mounted=False, result=None)
        data.update(values)
        return data

    def result(self, status='success', boot='old'):
        return dict(status=status, boot_id=boot, time=datetime.now(BERLIN).isoformat())

    def test_in_use_success_waits_for_new_boot_and_never_powers_off_previously_running_host(self):
        self.probe.side_effect = [self.state(), self.state(active='active'),
            self.state(result=self.result()), self.state(boot_id='new', result=self.result())]
        result = controller.on_host(self.host, self.record, None, True)
        self.assertEqual(result['status'], 'succeeded')
        self.assertEqual(self.probe.call_count, 4)
        self.start.assert_called_once()
        self.power.assert_not_called()

    def test_failed_client_is_left_running(self):
        self.probe.side_effect = [self.state(), self.state(result=self.result('failed'))]
        with self.assertRaisesRegex(RuntimeError, 'fehlgeschlagen'):
            controller.on_host(self.host, self.record, None, True)
        self.power.assert_not_called()

    def test_old_client_mode_is_not_silently_upgraded(self):
        self.probe.return_value = self.state(evening=False)
        result = controller.on_host(self.host, self.record, None, True)
        self.assertEqual(result['status'], 'skipped')
        self.start.assert_not_called()
        self.power.assert_not_called()

    def test_woken_free_client_is_powered_off_after_confirmed_reboot(self):
        self.reachable.return_value = False
        self.probe.side_effect = [self.state(uptime=0), self.state(boot_id='new', result=self.result())]
        self.power.return_value = 'Systemaktion eingeplant.'
        result = controller.on_host(self.host, self.record, None, True)
        self.assertTrue(result['woke'])
        self.wake.assert_called_once()
        self.power.assert_called_once_with(self.host, 'poweroff', None, maintenance_owner=True)

    def test_client_that_becomes_occupied_after_reboot_remains_on(self):
        self.reachable.return_value = False
        self.probe.side_effect = [self.state(uptime=0),
                                 self.state(boot_id='new', occupied=True, result=self.result())]
        controller.on_host(self.host, self.record, None, True)
        self.power.assert_not_called()

    def test_existing_daily_success_does_not_repeat_installation(self):
        self.probe.return_value = self.state(boot_id='new', result=self.result())
        with patch.object(controller, 'start_allowed', return_value=True):
            result = controller.on_host(self.host, self.record, None, False)
        self.assertEqual(result['status'], 'succeeded')
        self.start.assert_not_called()

    def test_manual_trial_cannot_reuse_an_earlier_success_as_its_own_reboot(self):
        previous = self.result(boot='earlier')
        previous['time'] = (datetime.now(BERLIN) - timedelta(minutes=5)).isoformat()
        self.probe.side_effect = [self.state(result=previous), self.state(result=previous),
                                 self.state(boot_id='new', result=self.result())]
        controller.on_host(self.host, self.record, None, True)
        self.assertEqual(self.probe.call_count, 3)

    def test_retry_does_not_wake_clients_already_finished_today(self):
        record = dict(started=datetime.now(BERLIN).isoformat(), results={
            'LAB-08A': {'status': 'succeeded'}, 'LAB-01A': {'status': 'failed'}})
        with patch.object(controller, 'history', return_value=[record]):
            self.assertEqual(controller.completed_today(), {'LAB-08A'})
        record['manual_pilot'] = True
        with patch.object(controller, 'history', return_value=[record]):
            self.assertEqual(controller.completed_today(), set())

    def test_closed_start_window_defers_new_work(self):
        self.probe.return_value = self.state()
        with patch.object(controller, 'start_allowed', return_value=False):
            result = controller.on_host(self.host, self.record, None, False)
        self.assertEqual(result['status'], 'deferred')
        self.start.assert_not_called()

    def test_remote_pilot_code_compiles_and_writes_real_unit_newlines(self):
        with patch.object(controller.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0)) as run:
            start_service(self.host, self.record, True)
        command = run.call_args.args[0][-1]
        import shlex
        code = shlex.split(command)[5]
        compile(code, '<remote-pilot>', 'exec')
        self.assertIn("file.write('[Service]\\nExecStart=\\n", code)

    def test_failed_wake_causality_is_not_accepted_as_new_poweron(self):
        self.assertFalse(controller.new_boot_after_wake(3600, 30))
        self.assertTrue(controller.new_boot_after_wake(15, 20))
