"""Manual power requests end sessions; automatic maintenance keeps its checks."""
import contextlib
import io
from pathlib import Path
import shlex
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from dashboard import room, room_action

HOST = 'LAB-03A'


class ManualPowerTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(room_action.os, 'geteuid', return_value=0))
        self.enterContext(patch.object(room_action.socket, 'gethostname', return_value=HOST))
        self.maintenance = self.enterContext(patch.object(room_action, 'maintenance_running', return_value=False))
        self.sessions = self.enterContext(patch.object(room_action, 'sessions', return_value=[('1', {'User':'1000'})]))
        self.home = self.enterContext(patch.object(room_action, 'home_mounted', return_value=True))
        self.run = self.enterContext(patch.object(room_action.subprocess, 'run'))
        self.enterContext(patch.object(room_action, 'source', 'fixture-source', create=True))
        self.output = io.StringIO()
        self.enterContext(contextlib.redirect_stdout(self.output))

    def invoke(self, action, *flags):
        with patch.object(sys, 'argv', ['room_action.py', HOST, action, *flags]):
            room_action.main()

    def test_manual_reboot_and_poweroff_ignore_sessions_homes_and_inhibitors(self):
        for action in ('reboot', 'poweroff'):
            with self.subTest(action=action):
                self.run.reset_mock()
                self.invoke(action, 'manual', 'deferred')
                self.run.assert_called_once_with(['systemctl','--no-block','--check-inhibitors=no',action],check=True)
        self.sessions.assert_not_called(); self.home.assert_not_called()

    def test_manual_request_retains_its_mode_in_delayed_command(self):
        self.invoke('reboot', 'manual')
        command = self.run.call_args.args[0]
        self.assertEqual(command[0], 'systemd-run')
        self.assertEqual(command[-4:], [HOST,'reboot','manual','deferred'])
        self.assertIn('Angemeldete Sitzungen werden beendet.', self.output.getvalue())
        self.sessions.assert_not_called(); self.home.assert_not_called()

    def test_automatic_poweroff_still_refuses_occupied_or_mounted_client(self):
        self.invoke('poweroff', 'deferred')
        self.run.assert_not_called()
        self.sessions.return_value = []
        self.invoke('poweroff', 'deferred')
        self.home.assert_called_once(); self.run.assert_not_called()
        self.home.return_value = False
        self.invoke('poweroff', 'deferred')
        self.run.assert_called_once_with(['systemctl','--no-block','poweroff'],check=True)

    def test_active_package_maintenance_still_blocks_manual_power(self):
        self.maintenance.return_value = True
        self.invoke('reboot', 'manual', 'deferred')
        self.run.assert_not_called()
        self.assertIn('Wartung läuft',self.output.getvalue())

    def test_manual_request_still_requires_the_verified_host_and_valid_mode(self):
        with patch.object(room_action.socket,'gethostname',return_value='different'), self.assertRaises(SystemExit):
            self.invoke('reboot','manual','deferred')
        with self.assertRaises(SystemExit): self.invoke('reboot','unknown')
        self.run.assert_not_called()

    def test_common_logout_accepts_cisco_autologin_and_regular_iserv_session(self):
        for uid,name,service in ((498,'cisco','sddm-autologin'),(2596,'s.test','sddm')):
            self.sessions.return_value=[('1',dict(User=str(uid),Type='x11',Service=service))]
            self.home.return_value=False
            user=SimpleNamespace(pw_uid=uid,pw_gid=42,pw_name=name,pw_dir='/var/lib/cisco-lab-local/cisco' if name=='cisco' else '/var/lib/iserv/client/home/s.test')
            with self.subTest(name=name),patch.object(room_action.pwd,'getpwuid',return_value=user), \
                    patch.object(room_action.grp,'getgrnam',return_value=SimpleNamespace(gr_gid=42)), \
                    patch.object(room_action.os,'getgrouplist',return_value=[42]), \
                    patch.object(room_action,'session_ids',return_value=[]), \
                    patch.object(room_action,'request_logout') as logout:
                self.invoke('logout');logout.assert_called_once_with(uid,42)
            self.assertIn('Abmeldung und Ende',self.output.getvalue())

    def test_common_logout_still_leaves_admin_root_and_multiple_sessions_alone(self):
        for uid,groups,count in ((0,[42],1),(1000,[1000],1),(498,[42],2)):
            self.sessions.return_value=[(str(index),dict(User=str(uid),Type='x11',Service='sddm-autologin')) for index in range(count)]
            user=SimpleNamespace(pw_uid=uid,pw_gid=42,pw_name='fixture',pw_dir='/fixture')
            with self.subTest(uid=uid,count=count),patch.object(room_action.pwd,'getpwuid',return_value=user), \
                    patch.object(room_action.grp,'getgrnam',return_value=SimpleNamespace(gr_gid=42)), \
                    patch.object(room_action.os,'getgrouplist',return_value=groups), \
                    patch.object(room_action,'request_logout') as logout:
                self.invoke('logout');logout.assert_not_called()

    def test_cisco_only_logout_does_not_end_an_iserv_student_session(self):
        self.sessions.return_value=[('1',dict(User='2596',Type='x11',Service='sddm'))]
        user=SimpleNamespace(pw_uid=2596,pw_gid=42,pw_name='s.test',pw_dir='/var/lib/iserv/client/home/s.test')
        with patch.object(room_action.pwd,'getpwuid',return_value=user),patch.object(room_action,'request_logout') as logout:
            self.invoke('demo-end');logout.assert_not_called()


class DispatchModeTests(unittest.TestCase):
    def test_only_manual_gui_power_gets_the_session_override(self):
        with TemporaryDirectory() as temporary:
            root=Path(temporary); known=root/'known_hosts'; known.write_text('fixture')
            actions=room.RoomActions.__new__(room.RoomActions)
            actions.deploys=SimpleNamespace(known_hosts_for=Mock(return_value=known))
            for owner in (False,True):
                with self.subTest(maintenance_owner=owner), patch.object(room,'reserved',return_value=False), \
                        patch.object(room.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout='Fixture',stderr='')) as command:
                    actions.on_host(HOST,'reboot',None,maintenance_owner=owner)
                    remote=shlex.split(command.call_args.args[0][-1])
                    self.assertEqual(remote[-3:] if not owner else remote[-2:], [HOST,'reboot','manual'] if not owner else [HOST,'reboot'])


if __name__ == '__main__': unittest.main()
