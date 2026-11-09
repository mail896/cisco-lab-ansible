"""Teacher access and power decisions must fail closed without touching a machine."""
from contextlib import nullcontext
from datetime import datetime
import json
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from deployment.apps.prepare_teacher_access import installer, refreshed_installer
from deployment.teacher import install, receiver
from maintenance import controller, teacher_controller as teacher
from maintenance.preview import BERLIN

TOKEN = 'a' * 32
BOOT = 'c9c05f21-3049-4184-b85b-5b5c5d433649'
NEW_BOOT = '95a38874-f698-4af4-85d1-f5ac96f1ccfe'


class InstallerTests(unittest.TestCase):
    def test_service_exception_is_narrow_and_idempotent(self):
        original = ('# existing configuration\nsession [default=1] pam_permit.so\n'
                    'session required pam_unix.so\nsession optional pam_mount.so\n'
                    'session optional pam_systemd.so\n')
        result = install.pam_content(original)
        self.assertEqual(result.replace(install.MOUNT_STACK, install.MOUNT), original)
        self.assertIn(install.MOUNT_STACK + '\n', result)
        self.assertNotIn('pam_localuser', result)
        self.assertEqual(install.pam_content(result), result)

    def test_unknown_mount_structure_requires_review(self):
        for text in ('session required pam_mount.so\n',
                     'session optional pam_mount.so\nsession optional pam_mount.so\n',
                     install.MOUNT_STACK + '\n' + install.MOUNT + '\n'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                install.pam_content(text)

    def test_existing_jumps_across_mount_are_preserved(self):
        original = ('session [success=3 default=1] pam_existing.so\n'
                    'session optional pam_other.so\n' + install.MOUNT + '\n'
                    'session required pam_tail.so\n')
        changed = install.pam_content(original)
        self.assertEqual(changed, original.replace(install.MOUNT, install.MOUNT_STACK))
        files = install.files('ssh-ed25519 PUBLIC', {'receiver': '', 'preview': '', 'software_client': '', 'deb_client': ''})
        self.assertEqual(files[Path('/etc/pam.d/cisco-lab-teacher-mount')][0],
                         (install.GUARD + '\n' + install.MOUNT + '\n').encode())

    def test_embedded_installer_is_standalone_and_help_has_no_side_effects(self):
        content = installer('ssh-ed25519 AAAATEST')
        scope = {'__name__': 'installer_test', '__file__': '/tmp/teacher-standalone.py'}
        exec(compile(content, '<installer>', 'exec'), scope)
        self.assertEqual(scope['PUBLIC_KEY'], 'ssh-ed25519 AAAATEST')
        self.assertEqual(refreshed_installer(content), content)
        self.assertEqual(set(scope['PAYLOAD']), {'receiver', 'preview', 'software_client', 'deb_client'})
        for name, code in scope['PAYLOAD'].items():
            compile(code, '<' + name + '>', 'exec')
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'installer.py'; path.write_bytes(content)
            result = subprocess.run(['/usr/bin/python3', '-IB', str(path), '--help'], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_installed_key_and_sudo_cannot_run_arbitrary_commands(self):
        files = install.files('ssh-ed25519 PUBLIC', {'receiver': '', 'preview': '', 'software_client': '', 'deb_client': ''})
        key = files[install.HOME / '.ssh/authorized_keys'][0].decode()
        self.assertTrue(key.startswith('restrict,from="203.0.113.200",command="/usr/local/sbin/cisco-lab-teacher-ssh" '))
        sudo = files[Path('/etc/sudoers.d/cisco-lab-teacher-app')][0].decode()
        self.assertEqual(sudo.strip(), 'cisco-lab-app ALL=(root) NOPASSWD: /usr/local/sbin/cisco-lab-teacher-endpoint ""')
        self.assertFalse(any(str(path).endswith('.timer') for path in files))
        unit = files[Path('/etc/systemd/system/cisco-lab-teacher-maintenance.service')][0].decode()
        self.assertNotIn('[Install]', unit)


class OccupancyTests(unittest.TestCase):
    def test_remote_ssh_does_not_block_but_local_sessions_and_ram_homes_do(self):
        ssh = dict(Name='marc', Class='user', Service='sshd', Remote='yes', Type='tty')
        greeter = dict(Name='sddm', Class='greeter', Service='sddm-greeter', Remote='no', Type='wayland')
        cases = [([ssh, greeter], '', False),
                 ([ssh, dict(ssh, Service='login', Remote='no')], '', True),
                 ([ssh, dict(ssh, Service='sddm', Remote='no', Type='wayland')], '', True),
                 ([dict(ssh, Type='x11')], '', True),
                 ([ssh], '/var/lib/iserv/client/home/test', True)]
        for sessions, mounts, expected in cases:
            def read(*args):
                if args[0] == 'findmnt':
                    return mounts
                if args[1] == 'list-sessions':
                    return '\n'.join(str(i) for i in range(len(sessions)))
                return '\n'.join(key + '=' + value for key, value in sessions[int(args[2])].items())
            with self.subTest(sessions=sessions, mounts=mounts), patch.object(receiver, 'read', side_effect=read):
                self.assertEqual(receiver.occupied(), expected)


class ReceiverTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(receiver, 'require_teacher'))
        self.enterContext(patch.object(receiver, 'lock', return_value=nullcontext()))
        self.enterContext(patch.object(receiver, 'boot_id', return_value=BOOT))
        self.occupied = self.enterContext(patch.object(receiver, 'occupied', return_value=False))
        self.enterContext(patch.object(receiver, 'apt_idle', return_value=True))
        self.enterContext(patch.object(receiver, 'package_locks_idle', return_value=True))
        self.enterContext(patch.object(receiver, 'active', return_value='inactive'))
        self.enterContext(patch.object(receiver, 'login_gate', return_value=nullcontext()))
        self.command = self.enterContext(patch.object(receiver.subprocess, 'run'))
        self.save = self.enterContext(patch.object(receiver, 'save'))
        self.load = self.enterContext(patch.object(receiver, 'load', return_value=None))

    def origin(self, **values):
        result = dict(id=TOKEN, boot_id=BOOT, woke=True, manual_pilot=True,
                      time=datetime.now(BERLIN).isoformat())
        result.update(values)
        return result

    def test_only_closed_commands_are_accepted(self):
        for value in ('status;id', 'status\n', 'start ' + TOKEN + ' ' + BOOT + ' woke;id',
                      'poweroff ' + TOKEN + ' ' + BOOT + ' extra', 'status ' + 'x' * 201,
                      'start ' + TOKEN + ' ' + BOOT, 'pilot ' + TOKEN + ' invalid woke'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                receiver.validate_arguments(value)
        self.assertEqual(receiver.validate_arguments('status'), ['status'])
        receiver.validate_arguments('pilot ' + TOKEN + ' ' + BOOT + ' already-on')
        receiver.validate_arguments('manual-poweroff ' + TOKEN + ' ' + BOOT)

    def test_status_advertises_closed_manual_poweroff_capability(self):
        with patch.object(receiver, 'boot_id', return_value=BOOT), \
                patch.object(receiver.Path, 'read_text', return_value='12.5 0'), \
                patch.object(receiver, 'active', return_value='inactive'), \
                patch.object(receiver, 'occupied', return_value=False):
            status = receiver.status()
        self.assertEqual(status['capabilities'], ['manual-poweroff'])

    def test_manual_poweroff_requires_free_teacher_and_acknowledges_before_shutdown(self):
        events=[]
        self.command.side_effect=lambda *args,**kwargs:events.append('poweroff')
        with TemporaryDirectory() as directory, patch.object(receiver,'RUNTIME',Path(directory)), \
                patch.object(receiver.time,'sleep'):
            response=receiver.dispatch('manual-poweroff '+TOKEN+' '+BOOT,
                acknowledge=lambda data:events.append(data))
        self.assertEqual(response,{'poweroff_requested':True,'id':TOKEN})
        self.assertEqual(events,[{'poweroff_requested':True,'id':TOKEN},'poweroff'])

    def test_manual_poweroff_refuses_real_package_lock(self):
        with patch.object(receiver,'package_locks_idle',return_value=False), self.assertRaises(RuntimeError):
            receiver.dispatch('manual-poweroff '+TOKEN+' '+BOOT)
        self.command.assert_not_called()

    def test_readonly_diagnosis_exposes_states_and_names_without_command_arguments(self):
        def read(*args):
            if args[:2] == ('loginctl', 'list-sessions'):
                return '8 1006 marc\n9 1999 private-person\n'
            return ('Id=' + args[2] + '\nName=' + ('marc' if args[2] == '8' else 'private-person')
                    + '\nClass=user\nService=sshd\nRemote=yes\nState=closing\nTTY=\nLeader=42')
        self.command.return_value = subprocess.CompletedProcess([], 0, stdout='42 1 ? Ss codex\n', stderr='')
        with patch.object(receiver, 'read', side_effect=read):
            result = receiver.session_diagnostics()
        self.assertEqual(result['sessions'][0]['State'], 'closing')
        self.assertEqual(result['sessions'][1]['account'], 'other')
        self.assertNotIn('private-person', json.dumps(result))
        self.assertEqual(result['operator_processes'][0]['command'], 'codex')
        self.assertEqual(self.command.call_args.args[0],
                         ['ps', '-u', 'marc', '-o', 'pid=,ppid=,tty=,stat=,comm='])
        self.assertFalse(result['processes_truncated'])

    def test_occupied_teacher_is_never_logged_out_or_started(self):
        self.occupied.return_value = True
        with self.assertRaises(RuntimeError):
            receiver.dispatch('pilot ' + TOKEN + ' ' + BOOT + ' woke')
        self.command.assert_not_called(); self.save.assert_not_called()

    def test_poweroff_rejects_already_on_failed_and_unrebooted_results(self):
        for origin, outcome in ((self.origin(woke=False), dict(status='success', reboot_required=False)),
                                (self.origin(), dict(status='failed', reboot_required=False)),
                                (self.origin(), dict(status='success', reboot_required=True))):
            outcome.update(id=TOKEN, boot_id=BOOT, time=datetime.now(BERLIN).isoformat())
            self.load.side_effect = lambda name: origin if name == 'request' else outcome
            with self.subTest(outcome=outcome), self.assertRaises(ValueError):
                receiver.dispatch('poweroff ' + TOKEN + ' ' + BOOT)
        self.command.assert_not_called()

    def test_successful_owned_request_can_poweroff_only_once(self):
        result = self.origin(status='success', reboot_required=False)
        origin = self.origin()
        self.load.side_effect = lambda name: origin if name == 'request' else result
        with TemporaryDirectory() as directory, patch.object(receiver, 'RUNTIME', Path(directory)):
            response = receiver.dispatch('poweroff ' + TOKEN + ' ' + BOOT)
        self.assertTrue(response['poweroff_requested'])
        self.command.assert_called_once_with(['systemctl', '--no-block', 'poweroff'], check=True, timeout=15)
        with self.assertRaises(ValueError):
            receiver.dispatch('poweroff ' + TOKEN + ' ' + BOOT)

    def test_poweroff_acceptance_precedes_the_command_that_can_terminate_ssh(self):
        result = self.origin(status='success', reboot_required=False)
        origin = self.origin()
        self.load.side_effect = lambda name: origin if name == 'request' else result
        events = []
        self.command.side_effect = lambda *args, **kwargs: events.append('poweroff')
        def acknowledge(reply):
            self.assertEqual(reply, dict(id=TOKEN, poweroff_requested=True))
            events.append('acknowledged')
        with TemporaryDirectory() as directory, patch.object(receiver, 'RUNTIME', Path(directory)), \
                patch.object(receiver.time, 'sleep'):
            receiver.dispatch('poweroff ' + TOKEN + ' ' + BOOT, acknowledge=acknowledge)
        self.assertEqual(events, ['acknowledged', 'poweroff'])

    def test_duplicate_worker_start_preserves_completed_result(self):
        origin = self.origin()
        self.load.side_effect = lambda name: origin if name == 'request' else self.origin(status='success')
        with patch.object(receiver, 'maintain') as maintain:
            receiver.run()
        maintain.assert_not_called(); self.save.assert_not_called()

    def test_uncertain_running_request_is_not_reinstalled(self):
        origin = self.origin()
        self.load.side_effect = lambda name: origin if name == 'request' else self.origin(status='running')
        with patch.object(receiver, 'maintain') as maintain, self.assertRaises(RuntimeError):
            receiver.run()
        maintain.assert_not_called()
        self.assertEqual(self.save.call_args.args[1]['status'], 'failed')

    def test_package_failure_never_requests_reboot(self):
        origin = self.origin()
        self.load.side_effect = lambda name: origin if name == 'request' else self.origin(status='pending')
        with patch.object(receiver, 'maintain', side_effect=RuntimeError('APT failed')):
            with self.assertRaisesRegex(RuntimeError, 'APT failed'):
                receiver.run()
        self.command.assert_not_called()
        self.assertEqual(self.save.call_args.args[1]['status'], 'failed')

    def test_patched_samba_upgrade_is_stopped_before_installation(self):
        with TemporaryDirectory() as directory:
            module = Path(directory) / 'module'; module.write_bytes(b'patched')
            digest = receiver.hashlib.sha256(b'patched').hexdigest()
            def read(*args):
                if args[:2] == ('apt-get', '-s'):
                    return 'Inst samba-libs:amd64 [old] (new repository)'
                return ''
            with (patch.object(receiver, 'MODULE', module), patch.object(receiver, 'PATCH_HASH', digest),
                  patch.object(receiver, 'automatic_installs_disabled', return_value=True),
                  patch.object(receiver, 'read', side_effect=read), patch.object(receiver, 'apt') as apt):
                with self.assertRaisesRegex(RuntimeError, 'Winbind-ABI'):
                    receiver.maintain(self.origin())
            apt.assert_called_once_with('-o', 'APT::Update::Error-Mode=any', 'update')


class TeacherTransportTests(unittest.TestCase):
    def test_shutdown_disconnect_requires_a_complete_matching_acceptance(self):
        with TemporaryDirectory() as directory:
            key, pin = Path(directory) / 'key', Path(directory) / 'pin'
            for path in (key, pin):
                path.touch(mode=0o600)
            replies = [('', False), ('{}', False),
                       (json.dumps(dict(id='b' * 32, poweroff_requested=True)), False),
                       (json.dumps(dict(id=TOKEN, poweroff_requested=True)) + '\n', True)]
            with patch.object(teacher, 'KEY', key), patch.object(teacher, 'PIN', pin), \
                    patch.object(teacher.os, 'getuid', return_value=key.stat().st_uid):
                for output, accepted in replies:
                    result = subprocess.CompletedProcess([], 255, output,
                        'Connection to 203.0.113.100 closed by remote host.')
                    with self.subTest(output=output), patch.object(teacher.subprocess, 'run', return_value=result):
                        if accepted:
                            self.assertTrue(teacher.request('poweroff ' + TOKEN + ' ' + BOOT)['poweroff_requested'])
                        else:
                            with self.assertRaises(RuntimeError):
                                teacher.request('poweroff ' + TOKEN + ' ' + BOOT)
                denied = subprocess.CompletedProcess([], 255, json.dumps(dict(id=TOKEN, poweroff_requested=True)),
                                                     'Permission denied (publickey).')
                with patch.object(teacher.subprocess, 'run', return_value=denied), self.assertRaises(RuntimeError):
                    teacher.request('poweroff ' + TOKEN + ' ' + BOOT)


class TeacherControllerTests(unittest.TestCase):
    def setUp(self):
        self.directory = self.enterContext(TemporaryDirectory())
        root = Path(self.directory)
        self.enterContext(patch.object(teacher, 'STATE', root))
        self.enterContext(patch.object(teacher, 'PENDING', root / 'pending.json'))
        self.enterContext(patch.object(teacher, 'CONFIG', root / 'configuration.json'))
        self.enterContext(patch.object(teacher.socket, 'gethostname', return_value='apps'))
        self.enterContext(patch.object(teacher.time, 'sleep'))
        self.enterContext(patch.object(teacher, 'configuration', return_value=dict(enabled=False)))
        self.online = self.enterContext(patch.object(teacher, 'reachable', return_value=True))
        self.probe = self.enterContext(patch.object(teacher, 'probe'))
        self.request = self.enterContext(patch.object(teacher, 'request'))
        self.wake = self.enterContext(patch.object(teacher.RoomActions, 'wake'))
        self.enterContext(patch.object(teacher.teacher_wake, 'configuration', return_value={'school_mac': 'verified'}))
        self.enterContext(patch.object(teacher.teacher_wake, 'route', return_value=('app', 'broadcast')))

    def state(self, *, token=None, woke=False, boot=BOOT, reboot=False, occupied=False, status='success'):
        return dict(version=1, host='LAB-Teacher', active='inactive', occupied=occupied,
                    boot_id=boot, uptime=0,
                    request=dict(id=token, boot_id=BOOT, woke=woke),
                    result=dict(id=token, boot_id=BOOT, status=status, reboot_required=reboot))

    def replies(self, *, woke=False, reboot=False, occupied=False, failed=False):
        def probe():
            if not teacher.PENDING.exists():
                return self.state()
            record = json.loads(teacher.PENDING.read_text())
            return self.state(token=record['id'], woke=woke, reboot=reboot,
                              boot=NEW_BOOT if reboot else BOOT, occupied=occupied,
                              status='failed' if failed else 'success')
        self.probe.side_effect = probe
        self.request.side_effect = lambda command: dict(id=command.split()[1], started=True, poweroff_requested=True)

    def test_disabled_automation_never_touches_teacher(self):
        self.assertEqual(teacher.run()['status'], 'disabled')
        self.probe.assert_not_called(); self.wake.assert_not_called(); self.request.assert_not_called()

    def test_human_session_defers_without_logout(self):
        self.probe.return_value = self.state(occupied=True)
        result = teacher.run(manual=True)
        self.assertEqual(result['status'], 'deferred')
        self.assertEqual(teacher.history()[0]['status'], 'deferred')
        self.request.assert_not_called()

    def test_deferred_woken_teacher_keeps_its_boot_origin_without_poweroff(self):
        self.online.return_value = False
        self.probe.return_value = self.state(occupied=True)
        result = teacher.run(manual=True)
        self.assertEqual(result['status'], 'deferred')
        self.assertTrue(result['woke'])
        self.assertFalse(result['initial_reachable'])
        self.assertEqual(result['boot_id'], BOOT)
        self.wake.assert_called_once()
        self.request.assert_not_called()

    def test_previously_online_teacher_stays_on(self):
        self.replies()
        result = teacher.run(manual=True)
        self.assertEqual(result['status'], 'succeeded')
        self.assertFalse(result['woke'])
        self.assertEqual(self.request.call_count, 1)
        self.wake.assert_not_called()

    def test_owned_wake_and_required_reboot_allow_shutdown(self):
        self.online.return_value = False
        self.replies(woke=True, reboot=True)
        result = teacher.run(manual=True)
        self.assertEqual(result['status'], 'succeeded', result)
        self.assertEqual(result['final_boot_id'], NEW_BOOT)
        self.wake.assert_called_once()
        self.assertTrue(self.request.call_args.args[0].startswith('poweroff '))
        self.assertEqual(self.online.call_count, 4)

    def test_occupied_after_reboot_teacher_stays_on(self):
        self.online.return_value = False
        self.replies(woke=True, reboot=True, occupied=True)
        result = teacher.run(manual=True)
        self.assertEqual(result['status'], 'succeeded')
        self.assertEqual(self.request.call_count, 1)

    def test_failure_leaves_teacher_on(self):
        self.replies(failed=True)
        result = teacher.run(manual=True)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(self.request.call_count, 1)

    def test_mismatched_boot_or_origin_cannot_authorize_completion(self):
        record = dict(id=TOKEN, boot_id=BOOT, woke=False)
        for value in (self.state(token=TOKEN, woke=True), self.state(token='b' * 32),
                      self.state(token=TOKEN, boot=NEW_BOOT)):
            with self.subTest(value=value), self.assertRaises(RuntimeError):
                teacher.verified_result(record, value)

    def test_restart_resumes_monitoring_without_restarting_maintenance(self):
        teacher.save(teacher.PENDING, dict(id=TOKEN, boot_id=BOOT, woke=False, stage='maintenance',
                                         status='running', manual_pilot=True, started=datetime.now(BERLIN).isoformat()))
        self.probe.return_value = self.state(token=TOKEN)
        self.assertEqual(teacher.run(manual=True)['status'], 'succeeded')
        self.request.assert_not_called(); self.wake.assert_not_called()

    def test_restart_after_confirmed_poweroff_does_not_wake_teacher_again(self):
        teacher.save(teacher.PENDING, dict(id=TOKEN, stage='poweroff-confirming', status='running',
                                         manual_pilot=True, started=datetime.now(BERLIN).isoformat()))
        self.online.return_value = False
        self.assertEqual(teacher.run(manual=True)['status'], 'succeeded')
        self.probe.assert_not_called(); self.request.assert_not_called(); self.wake.assert_not_called()

    def activation_proof(self, **changes):
        proof = dict(id=TOKEN, status='succeeded', manual_pilot=True, woke=True, initial_reachable=False,
                     poweroff_verified=True, poweroff_acknowledged=True,
                     implementation_sha256=teacher.implementation_signature(), target_sha256=teacher.target_signature(),
                     started=datetime.now(BERLIN).isoformat(), finished=datetime.now(BERLIN).isoformat(),
                     result=dict(status='success', boot_id=BOOT, reboot_required=True),
                     boot_id=BOOT, final_boot_id=NEW_BOOT)
        proof.update(changes)
        teacher.save(teacher.STATE / ('run-' + TOKEN + '.json'), proof)

    def test_activation_requires_real_s5_maintenance_and_poweroff_proof(self):
        with self.assertRaises(ValueError):
            teacher.activate_after_pilot()
        for missing in ('manual_pilot', 'woke', 'poweroff_acknowledged', 'poweroff_verified'):
            self.activation_proof(**{missing: False})
            with self.subTest(missing=missing), self.assertRaises(ValueError):
                teacher.activate_after_pilot()
        self.assertFalse(teacher.CONFIG.exists())
        self.request.assert_not_called(); self.wake.assert_not_called()

    def test_changed_code_or_target_cannot_reuse_old_pilot(self):
        for value in ({'implementation_sha256': 'old-code'}, {'target_sha256': 'old-target'},
                      {'final_boot_id': BOOT}):
            self.activation_proof(**value)
            with self.subTest(value=value), self.assertRaises(ValueError):
                teacher.activate_after_pilot()
        self.assertFalse(teacher.CONFIG.exists())

    def test_successful_proof_enables_only_its_current_configuration(self):
        self.activation_proof()
        self.assertTrue(teacher.activate_after_pilot()['enabled'])
        self.assertEqual(json.loads(teacher.CONFIG.read_text()),
                         dict(version=1, enabled=True, pilot_verified=True, s5_verified=True))
        self.request.assert_not_called(); self.wake.assert_not_called()


class ClientBarrierTests(unittest.TestCase):
    def test_missing_failed_or_manual_clients_block_teacher_followup(self):
        record = dict(status='finished', results={'LAB-08A': {'status': 'succeeded'}})
        with (patch.object(controller.socket, 'gethostname', return_value='apps'),
              patch.object(teacher, 'enabled', return_value=True), patch.object(teacher, 'run') as run,
              patch.object(controller, 'targets', return_value={'LAB-08A': {}, 'LAB-08B': {}}),
              patch.object(controller, 'completed_today', return_value=set())):
            self.assertEqual(controller.teacher_followup(record, False)['status'], 'blocked')
            self.assertIsNone(controller.teacher_followup(record, True))
            record['results']['LAB-08B'] = {'status': 'failed'}
            record['status'] = 'failed'
            self.assertEqual(controller.teacher_followup(record, False)['status'], 'blocked')
            run.assert_not_called()

    def test_earlier_daily_successes_and_current_clients_form_full_barrier(self):
        record = dict(status='finished', results={'LAB-08B': {'status': 'succeeded'}})
        with (patch.object(controller.socket, 'gethostname', return_value='apps'),
              patch.object(teacher, 'enabled', return_value=True), patch.object(teacher, 'run') as run,
              patch.object(controller, 'targets', return_value={'LAB-08A': {}, 'LAB-08B': {}}),
              patch.object(controller, 'completed_today', return_value={'LAB-08A'})):
            controller.teacher_followup(record, False)
            run.assert_called_once_with()


if __name__ == '__main__':
    unittest.main()
