"""Closed Teacher package protocol and shared profile integration; no live installs."""
from contextlib import nullcontext
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from dashboard import software_client as client
from dashboard import deb_client
from dashboard.teacher_software import TeacherSoftware, HOST
from deployment.teacher import receiver, install
from maintenance import teacher_controller


class ReceiverTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.dict(sys.modules, software_client=client,deb_client=deb_client))
        self.enterContext(patch.object(receiver, 'require_teacher'))
        self.lock = self.enterContext(patch.object(receiver, 'lock', side_effect=lambda _: nullcontext()))
        self.active = self.enterContext(patch.object(receiver, 'active', return_value='inactive'))
        self.apt = self.enterContext(patch.object(receiver, 'apt_idle', return_value=True))
        self.read = self.enterContext(patch.object(receiver, 'read', return_value=''))
        self.occupied = self.enterContext(patch.object(receiver, 'occupied', return_value=True))
        self.plan = self.enterContext(patch.object(client, 'plan', return_value={'fixture': True}))
        self.install = self.enterContext(patch.object(client, 'install', return_value={'installed': ['gimp']}))
        self.revision = hashlib.sha256(Path(client.__file__).read_bytes()+Path(deb_client.__file__).read_bytes()).hexdigest()

    def payload(self, **fields):
        return dict(operation='plan', revision=self.revision, packages=['gimp'], **fields)

    def test_closed_schema_and_exact_revision_cannot_run_other_commands(self):
        for data in ([], {}, dict(operation='shell', revision=self.revision),
                     dict(self.payload(), packages=['gimp;id']), dict(self.payload(), source='print(1)'),
                     dict(self.payload(), revision='a'*64), dict(self.payload(), operation='install'),
                     dict(self.payload(), operation='install', expected='')):
            with self.subTest(data=data), self.assertRaises(ValueError):receiver.software_dispatch(data)
        self.plan.assert_not_called();self.install.assert_not_called()

    def test_manual_profile_works_with_desktop_but_never_logs_out_or_reboots(self):
        with patch.object(receiver, 'login_gate', side_effect=AssertionError('must not log out')), \
                patch.object(receiver.subprocess, 'run', side_effect=AssertionError('must not call systemctl')):
            self.assertEqual(receiver.software_dispatch(self.payload()), {'fixture': True})
            data = dict(self.payload(), operation='install', expected='b'*64)
            self.assertEqual(receiver.software_dispatch(data), {'installed': ['gimp']})
        self.plan.assert_called_once_with(['gimp'], HOST)
        self.install.assert_called_once_with(['gimp'], HOST, 'b'*64)
        self.occupied.assert_not_called()
        self.assertEqual([call.args[0] for call in self.lock.call_args_list], ['endpoint', 'worker']*2)

    def test_active_maintenance_and_apt_and_broken_dpkg_block_package_changes(self):
        for phase in ('maintenance', 'apt', 'dpkg'):
            self.active.return_value = 'active' if phase=='maintenance' else 'inactive'
            self.apt.return_value = phase!='apt'
            self.read.return_value = 'broken' if phase=='dpkg' else ''
            with self.subTest(phase=phase), self.assertRaises(RuntimeError):
                receiver.software_dispatch(self.payload())
        self.plan.assert_not_called();self.install.assert_not_called()

    def test_installed_wrapper_forwards_only_software_body_and_retains_legacy_protocol(self):
        files=install.files('ssh-ed25519 PUBLIC',dict(receiver='',preview='',software_client='',deb_client=''))
        source=files[Path('/usr/local/sbin/cisco-lab-teacher-ssh')][0]
        for command, body, forwarded in [('status','ignored','status'),('software','{"operation":"capabilities"}',
                                         'software\n{"operation":"capabilities"}')]:
            with self.subTest(command=command), patch.dict('os.environ', SSH_ORIGINAL_COMMAND=command), \
                    patch('sys.stdin', io.StringIO(body)), patch('subprocess.run', return_value=Mock(returncode=0)) as run:
                with self.assertRaises(SystemExit) as stop:exec(compile(source, '<wrapper>', 'exec'), {})
                self.assertEqual(stop.exception.code, 0)
                self.assertEqual(run.call_args.kwargs['input'], forwarded)
                self.assertEqual(run.call_args.args[0],['/usr/bin/sudo','-n','/usr/local/sbin/cisco-lab-teacher-endpoint'])


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.root=Path(self.enterContext(TemporaryDirectory()))
        self.pin=self.root/'pin';self.pin.write_text('fixture');self.pin.chmod(0o600)
        self.enterContext(patch.object(teacher_controller, 'PIN', self.pin))
        self.adapter=TeacherSoftware(self.root/'access.json')
        self.command=self.enterContext(patch.object(teacher_controller, 'connection', return_value=['ssh','pinned','software']))
        self.run=self.enterContext(patch('dashboard.teacher_software.subprocess.run'))

    def ready(self):
        self.run.return_value=subprocess.CompletedProcess([],0,json.dumps(dict(host=HOST,protocol=2,revision=self.adapter.revision())))
        self.assertTrue(self.adapter.verify()['ready'])

    def test_state_is_local_and_readiness_requires_current_helper_and_pin(self):
        self.assertFalse(self.adapter.state()['ready']);self.run.assert_not_called()
        self.ready()
        self.run.reset_mock();self.assertTrue(self.adapter.state()['ready']);self.run.assert_not_called()
        self.pin.write_text('changed');self.assertFalse(self.adapter.state()['ready'])
        self.ready()
        with patch.object(self.adapter,'revision',return_value='a'*64):self.assertFalse(self.adapter.state()['ready'])

    def test_install_uses_only_fixed_command_json_and_confirmed_digest(self):
        self.ready()
        self.adapter.execute('install',['gimp'],'b'*64)
        self.command.assert_called_with('software')
        call=self.run.call_args
        self.assertEqual(call.args[0], ['ssh','pinned','software'])
        self.assertEqual(json.loads(call.kwargs['input']),dict(operation='install',revision=self.adapter.revision(),packages=['gimp'],expected='b'*64))
        self.assertEqual(call.kwargs['timeout'],3600)

    def test_old_receiver_and_offline_teacher_stay_unavailable(self):
        self.run.return_value=subprocess.CompletedProcess([],1,'','Nur feste Teacher-Wartungsaufträge erlaubt.')
        self.assertFalse(self.adapter.verify()['ready'])
        with self.assertRaises(ValueError):self.adapter.execute('plan',['gimp'])
        self.run.side_effect=subprocess.TimeoutExpired(['ssh'],25)
        state=self.adapter.verify();self.assertFalse(state['ready']);self.assertIn('erreichbar',state['error'])
