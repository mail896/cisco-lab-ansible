"""Offline tests only: fake sysfs, dialog mocks, temporary files and PTYs."""
import importlib.util
import os
from pathlib import Path
import pty
import select
import shutil
import subprocess
import tempfile
import termios
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('lab_serial', ROOT / 'roles/lab_serial/files/lab_serial_console.py')
serial = importlib.util.module_from_spec(spec)
import sys
sys.modules[spec.name] = serial
spec.loader.exec_module(serial)


class SerialTests(unittest.TestCase):
    def test_no_device_and_single_device(self):
        with self.assertRaises(serial.ConsoleError):
            serial.select_adapter([])
        adapter = serial.Adapter('/dev/ttyUSB7', 'console')
        def no_dialog(*args, **kwargs):
            self.fail('One adapter must not open a menu')
        self.assertEqual(serial.select_adapter([adapter], run=no_dialog), adapter)

    def test_menu_selection_cancel_and_untrusted_response(self):
        devices = [serial.Adapter('/dev/ttyUSB1', 'USB one'), serial.Adapter('/dev/ttyACM9', 'USB two')]
        for output, code, expected in [('1\n', 0, devices[1]), ('', 1, None)]:
            def dialog(args, **kwargs):
                self.assertIsInstance(args, list)
                self.assertEqual(args[0], '/usr/bin/kdialog')
                return subprocess.CompletedProcess(args, code, output)
            self.assertEqual(serial.select_adapter(devices, run=dialog), expected)
        for answer in ['99', '-1', '0;touch /tmp/bad']:
            with self.assertRaises(serial.ConsoleError):
                serial.select_adapter(devices, run=lambda *a, **k: subprocess.CompletedProcess(a, 0, answer))

    def test_requested_device_must_still_be_present(self):
        with self.assertRaises(serial.ConsoleError):
            serial.select_adapter([serial.Adapter('/dev/ttyUSB0', 'one')], '/dev/ttyUSB1')

    def test_usb_backed_device_discovery_and_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            dev = base / 'dev'; dev.mkdir()
            sys_tty = base / 'sys'; sys_tty.mkdir()
            usb = base / 'usb'; usb.mkdir()
            for key, value in [('idVendor','1234'), ('idProduct','5678'), ('product','Cisco console'), ('serial','serial-42')]:
                (usb / key).write_text(value)
            for name in ('ttyUSB0', 'ttyACM3', 'ttyS0', 'ttyUSBbad'):
                (dev / name).touch()
                folder = sys_tty / name; folder.mkdir()
                (folder / 'device').symlink_to(usb)
            with patch.object(serial.stat, 'S_ISCHR', return_value=True):
                found = serial.discover(dev, sys_tty)
            self.assertEqual(len(found), 2)
            self.assertTrue(all('serial-42' in a.label for a in found))
            self.assertEqual({Path(a.device).name for a in found}, {'ttyUSB0', 'ttyACM3'})

    def test_group_membership_and_effective_access(self):
        group = serial.grp.getgrgid(os.getgid()).gr_name
        adapter = serial.Adapter('/dev/ttyUSB0', 'test')
        serial.check_access(adapter, group, groups=[os.getgid()], access=lambda *a, **k: True)
        with self.assertRaises(serial.ConsoleError):
            serial.check_access(adapter, group, groups=[], access=lambda *a, **k: True)
        with self.assertRaises(serial.ConsoleError):
            serial.check_access(adapter, group, groups=[os.getgid()], access=lambda *a, **k: False)

    def test_lock_rejects_concurrent_console_and_releases_after_exit(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            adapter = serial.Adapter('/dev/ttyUSB0', 'test')
            with serial.device_lock(adapter, base, base):
                with self.assertRaises(serial.ConsoleError):
                    with serial.device_lock(adapter, base, base):
                        self.fail('Second console entered')
            with serial.device_lock(adapter, base, base):
                pass
            self.assertTrue((base / 'ttyUSB0.lock').exists())
            (base / 'LCK..ttyUSB0').write_text('1234')
            with self.assertRaises(serial.ConsoleError):
                with serial.device_lock(adapter, base, base):
                    pass
            self.assertTrue((base / 'LCK..ttyUSB0').exists())

    def test_dedicated_profile_preserves_defaults_and_refuses_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            defaults = base / '.minirc.dfl'; defaults.write_text('personal settings')
            serial.write_minicom_profile(base)
            profile = base / '.minirc.lab-cisco'
            self.assertEqual(defaults.read_text(), 'personal settings')
            before = profile.stat().st_mtime_ns
            serial.write_minicom_profile(base)
            self.assertEqual(before, profile.stat().st_mtime_ns)
            profile.write_text('old lab settings')
            serial.write_minicom_profile(base)
            backup = list((base / '.config/cisco-lab-backups/block02').iterdir())
            self.assertEqual(backup[0].read_text(), 'old lab settings')
            profile.unlink(); profile.symlink_to(defaults)
            with self.assertRaises(serial.ConsoleError):
                serial.write_minicom_profile(base)

    def test_commands_use_selected_port_no_shell_no_init_bypass(self):
        self.assertEqual(serial.app_command('putty', '/dev/ttyACM4'),
                         ['/usr/bin/putty','-serial','/dev/ttyACM4','-sercfg','9600,8,n,1,N'])
        command = serial.app_command('minicom', '/dev/ttyUSB9')
        self.assertIn('/dev/ttyUSB9', command)
        self.assertNotIn('-o', command)  # preserve Minicom's own lock checks

    @unittest.skipUnless(shutil.which('minicom'), 'Minicom not installed locally')
    def test_real_minicom_with_pseudoterminal_and_no_router_attached(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            serial.write_minicom_profile(base)
            master, slave = pty.openpty()
            screen_master, screen_slave = pty.openpty()
            env = os.environ.copy(); env['HOME'] = str(base); env['TERM'] = 'xterm'; env.pop('MINICOM', None)
            lock = Path('/run/lock') / ('LCK..' + os.ttyname(slave).removeprefix('/dev/').replace('/', '_'))
            if lock.exists():
                self.skipTest('Existing UUCP lock for allocated test PTY')
            process = subprocess.Popen(serial.app_command('minicom', os.ttyname(slave)),
                                       stdin=screen_slave, stdout=screen_slave, stderr=screen_slave,
                                       env=env, start_new_session=True)
            try:
                # Read screen output until Minicom initialized the port, at most 3s.
                deadline = time.monotonic() + 3
                screen = b''
                while time.monotonic() < deadline:
                    ready, _, _ = select.select([screen_master], [], [], 0.1)
                    if ready:
                        screen += os.read(screen_master, 65536)
                    if b'9600' in screen:
                        break
                self.assertIsNone(process.poll(), screen.decode(errors='replace'))
                attrs = termios.tcgetattr(slave)
                self.assertEqual(attrs[4], termios.B9600)
                self.assertEqual(attrs[5], termios.B9600)
                self.assertFalse(attrs[2] & (termios.PARENB | termios.CSTOPB | termios.CRTSCTS))
                self.assertFalse(attrs[0] & (termios.IXON | termios.IXOFF))
                ready, _, _ = select.select([master], [], [], 0.2)
                self.assertFalse(ready, 'Unexpected modem initialization bytes sent')
            finally:
                process.terminate()
                try: process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill(); process.wait()
                if lock.exists() and lock.read_text().strip() == str(process.pid):
                    lock.unlink()  # remove only the lock created by this test process
                for fd in (master,slave,screen_master,screen_slave): os.close(fd)


if __name__ == '__main__':
    unittest.main()
