"""Intake and Teacher room boundaries, with no connections to LAB devices."""
import base64
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
from time import monotonic, sleep
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from dashboard import admission, collect_device, intake, provision, room
from maintenance import controller, teacher_wake


def device(host='LAB-08B'):
    return dict(host=host, address=intake.LAB_TARGETS[host], protected=['203.0.113.0/24'], adapters=[
        dict(interface='eno1', mac='02:11:22:33:44:55', uuid='11111111-1111-1111-1111-111111111111',
             usb=False, school=True),
        dict(interface='enx001122334466', mac='02:11:22:33:44:66', uuid='22222222-2222-2222-2222-222222222222',
             usb=True, school=False)])


class IntakeTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(TemporaryDirectory()))
        self.admissions = self.root / 'admissions'; self.admissions.mkdir()
        self.hardware = self.root / 'hardware'
        for module in (admission, provision, intake):
            self.enterContext(patch.object(module, 'ADMISSIONS', self.admissions))
        for module in (admission, intake):
            self.enterContext(patch.object(module, 'HARDWARE', self.hardware))
        self.enterContext(patch.object(controller, 'targets', return_value={'LAB-08A': 'test'}))
        self.deploys = SimpleNamespace(lock=threading.Lock())
        self.queue = provision.Provision(self.root / 'runs', self.deploys, 'apps')
        self.intake = self.queue.intake

    def completed_read(self, host='LAB-08B'):
        run = dict(id='a'*32, host=host, status='succeeded', started=self.queue.now(),
                   finished=self.queue.now(), student='student', fingerprint='SHA256:'+'a'*43,
                   pin='test pin\n', device=device(host))
        self.intake.save(run)
        return run

    def test_first_use_pins_without_manual_fingerprint(self):
        with patch.object(intake, 'scan_key', return_value=('new pin\n', 'SHA256:'+'a'*43)), \
                patch.object(threading, 'Thread') as thread:
            run = self.intake.start('LAB-08B', 'ephemeral', 'student', True)
            self.assertEqual(run['status'], 'running')
            self.assertNotIn('pin', run)
            saved = self.intake.records()[0]
            self.assertNotIn('ephemeral', json.dumps(saved))
            self.assertTrue(self.deploys.lock.locked())
            thread.assert_called_once()
            self.deploys.lock.release()

    def until(self, condition):
        deadline = monotonic() + 5
        while not condition():
            self.assertLess(monotonic(), deadline)
            sleep(.01)

    def test_bulk_read_capacity_partial_failure_and_secret_lifetime(self):
        hosts = ['LAB-01A', 'LAB-01B', 'LAB-02A', 'LAB-02B', 'LAB-03A']
        started = []; release = threading.Event()
        def read(host, pin, password):
            self.assertEqual(password, 'ephemeral')
            started.append(host); release.wait(4)
            if host == hosts[0]:
                raise intake.ReadError('usb-adapter')
            return device(host)
        # A smaller fixture capacity exercises queuing as well as partial failure.
        with patch.object(provision, 'FRESH_WORKERS', 4), \
                patch.object(intake, 'scan_key', return_value=('pin\n','SHA256:'+'a'*43)), \
                patch.object(intake, 'read_device', side_effect=read):
            try:
                result = self.intake.start_many(hosts, 'ephemeral', True, {})
                self.until(lambda: len(started) == 4)
                self.assertTrue(self.deploys.lock.locked())
                self.assertEqual(sum(row['status']=='queued' for row in self.intake.records()),1)
                self.assertNotIn('pin', json.dumps(result))
            finally:
                release.set(); self.until(lambda: not self.deploys.lock.locked())
        records = self.intake.records()
        self.assertEqual(sum(row['status']=='succeeded' for row in records),4)
        self.assertEqual(next(row for row in records if row['host']==hosts[0])['code'],'usb-adapter')
        self.assertNotIn('ephemeral', json.dumps(records))

    def test_bulk_changed_pin_never_uses_password_until_explicit_confirmation(self):
        self.completed_read('LAB-01A')
        with patch.object(intake, 'scan_key', return_value=('changed\n','SHA256:'+'b'*43)), \
                patch.object(intake, 'read_device', side_effect=lambda host, pin, pwd: device(host)) as read:
            for attempt in range(2):
                self.intake.start_many(['LAB-01A','LAB-01B'], 'ephemeral', True, {})
                self.until(lambda: not self.deploys.lock.locked())
                latest = next(row for row in self.intake.records() if row['host']=='LAB-01A')
                self.assertEqual(latest['status'],'key_changed')
                self.assertNotIn('pin',latest)
            self.assertTrue(all(call.args[0]=='LAB-01B' for call in read.call_args_list))
            self.intake.start_many(['LAB-01A'], 'ephemeral', True, {'LAB-01A':'SHA256:'+'b'*43})
            self.until(lambda: not self.deploys.lock.locked())
            self.assertEqual(read.call_args.args[0],'LAB-01A')

    def test_bulk_invalid_inputs_do_not_start_connections(self):
        with patch.object(intake, 'scan_key') as scan:
            for hosts in ([], ['LAB-08A'], ['IServ'], ['LAB-01A']*2, [None], 'LAB-01A'):
                with self.subTest(hosts=hosts), self.assertRaises(ValueError):
                    self.intake.start_many(hosts, 'ephemeral', True, {})
            scan.assert_not_called()
        self.assertFalse(self.deploys.lock.locked())

    def test_bulk_setup_reuses_real_bootstrap_and_keeps_successes(self):
        targets = []
        for index,host in enumerate(['LAB-01A','LAB-01B','LAB-02A']):
            row = self.completed_read(host)
            # completed_read uses a fixed fixture id; keep a separate record per host.
            row['id'] = format(index+1,'032x'); self.intake.save(row)
            (self.intake.directory / ('read-'+'a'*32+'.json')).unlink()
            targets.append(dict(run_id=row['id'],lab_interface='enx001122334466'))
        def ansible(host, settings, passwords):
            self.assertTrue(self.deploys.lock.locked())
            self.assertEqual(passwords,('ephemeral','ephemeral'))
            return int(host=='LAB-01B')
        with patch.object(intake,'verified_key',return_value='pin\n'), \
                patch.object(self.queue.bootstrap,'run_ansible',side_effect=ansible):
            result = self.intake.setup_many(targets,'ephemeral','ephemeral',True,'student')
            self.until(lambda:not self.deploys.lock.locked())
        self.assertEqual(len(result['runs']),3)
        self.assertEqual({row['host']:row['status'] for row in self.queue.records()},
                         {'LAB-01A':'succeeded','LAB-01B':'failed','LAB-02A':'succeeded'})
        self.assertTrue((self.admissions/'LAB-01A.json').exists())
        self.assertFalse((self.admissions/'LAB-01B.json').exists())
        for path in self.root.rglob('*.json'):
            self.assertNotIn('ephemeral',path.read_text())

    def test_bulk_setup_rechecks_key_before_password_use(self):
        row = self.completed_read()
        with patch.object(intake,'verified_key',side_effect=ValueError('changed')), \
                patch.object(self.queue.bootstrap,'run_ansible') as ansible:
            self.intake.setup_many([dict(run_id=row['id'],lab_interface='enx001122334466')],
                                   'ephemeral','ephemeral',True,'student')
            self.until(lambda:not self.deploys.lock.locked())
            ansible.assert_not_called()
        self.assertEqual(self.queue.records()[0]['status'],'failed')

    def test_device_read_needs_no_student_but_baseline_preparation_does(self):
        with patch.object(intake,'scan_key',return_value=('new pin\n','SHA256:'+'a'*43)),patch.object(threading,'Thread'):
            run=self.intake.start('LAB-08B','ephemeral','',True)
        self.deploys.lock.release()
        run.update(status='succeeded',finished=self.queue.now(),device=device())
        self.intake.save(dict(run,pin='new pin\n'))
        with self.assertRaisesRegex(ValueError,'IServ-Schülerkonto'):
            self.intake.prepare(run['id'],'enx001122334466',True)
        self.assertFalse(self.hardware.exists())
        self.intake.prepare(run['id'],'enx001122334466',True,student='student')
        self.assertEqual(admission.read_settings(self.hardware/'LAB-08B.json')['lab_fresh_student'],'student')

    def test_changed_key_requires_confirmation_before_any_password_use(self):
        self.completed_read()
        with patch.object(intake, 'scan_key', return_value=('changed pin\n', 'SHA256:'+'b'*43)), \
                patch.object(threading, 'Thread') as thread:
            run = self.intake.start('LAB-08B', 'ephemeral', 'student', True)
            self.assertTrue(run['needs_key_confirmation'])
            self.assertFalse(self.deploys.lock.locked())
            thread.assert_not_called()
            run = self.intake.start('LAB-08B', 'ephemeral', 'student', True, 'SHA256:'+'b'*43)
            self.assertEqual(run['status'], 'running')
            self.deploys.lock.release()

    def test_invalid_target_or_missing_image_never_scans(self):
        with patch.object(intake, 'scan_key') as scan:
            for host, confirmed in [('LAB-08A', True), ('IServ', True), ('LAB-08B', False), ([], True)]:
                with self.subTest(host=host), self.assertRaises(ValueError):
                    self.intake.start(host, 'secret', 'student', confirmed)
            scan.assert_not_called()

    def test_device_validation_rejects_mismatch_duplicates_and_extra_vars(self):
        good = device()
        self.assertEqual(intake.validate_device('LAB-08B', good), good)
        for mutate in (lambda x: x.update(host='LAB-01A'),
                       lambda x: x.update(ansible_connection='local'),
                       lambda x: x['adapters'][1].update(mac=x['adapters'][0]['mac']),
                       lambda x: x['adapters'][0].update(usb=True),
                       lambda x: x.update(protected=['203.0.113.200/24'])):
            wrong = deepcopy(good); mutate(wrong)
            with self.assertRaises(ValueError): intake.validate_device('LAB-08B', wrong)

    def test_hardware_has_only_server_controlled_settings(self):
        run = self.completed_read()
        self.intake.prepare(run['id'], 'enx001122334466', True)
        data = admission.read_settings(self.hardware / 'LAB-08B.json')
        self.assertEqual(data['lab_fresh_packettracer_deb'], intake.PACKAGE)
        self.assertIs(data['lab_fresh_rebuild_08b'], True)
        self.assertEqual(set(data), {'lab_fresh_host', 'lab_fresh_confirmed', 'lab_fresh_student',
                                    'lab_fresh_packettracer_deb', 'lab_fresh_rebuild_08b', 'lab_network_manifest'})
        with self.assertRaises(ValueError): self.intake.prepare(run['id'], 'eno1', True)

    def test_expired_read_or_newer_identity_is_not_used(self):
        run = self.completed_read(); run['finished'] = '2026-01-01T00:00:00+01:00'; self.intake.save(run)
        with self.assertRaises(ValueError): self.intake.prepare(run['id'], 'enx001122334466', True)
        old = self.completed_read(); new = dict(old, id='b'*32, started=self.queue.now()); self.intake.save(new)
        with self.assertRaises(ValueError): self.intake.prepare(old['id'], 'enx001122334466', True)

    def test_failure_releases_lock_without_persisting_password(self):
        run = self.completed_read(); self.deploys.lock.acquire()
        with patch.object(intake, 'read_device', side_effect=ValueError('internal sensitive detail')):
            self.intake.run(run, 'ephemeral')
        self.assertFalse(self.deploys.lock.locked())
        self.assertEqual(run['status'], 'failed')
        self.assertNotIn('ephemeral', json.dumps(self.intake.records()))
        self.assertNotIn('sensitive detail', json.dumps(self.intake.records()))

    def test_known_failure_keeps_safe_actionable_message_after_reload(self):
        run = self.completed_read(); self.deploys.lock.acquire()
        with patch.object(intake, 'read_device', side_effect=intake.ReadError('usb-adapter')):
            self.intake.run(run, 'ephemeral')
        saved = self.intake.state()['reads'][0]
        self.assertEqual(saved['code'], 'usb-adapter')
        self.assertIn('Adapter anschließen', saved['error'])
        self.assertFalse(self.deploys.lock.locked())

    def test_remote_errors_are_mapped_without_echoing_remote_text(self):
        cases = [
            (0, intake.MARKER + json.dumps(dict(ok=False, code='usb-adapter', error='secret')), '', 'usb-adapter'),
            (0, intake.MARKER + json.dumps(dict(ok=False, code='secret', error='secret')), '', 'failed'),
            (0, intake.MARKER + json.dumps(dict(ok=True, device={'secret': 'secret'})), '', 'device-data'),
            (1, '', 'Permission denied: secret', 'ssh-auth'),
            (1, '', 'ssh_askpass: exec(ansible): No such file or directory\nPermission denied: secret','ssh-helper'),
            (1, '', 'Host key verification failed: secret', 'ssh-identity'),
            (1, '', 'Connection refused: secret', 'connection'),
        ]
        for rc, stdout, stderr, code in cases:
            with self.subTest(code=code), patch.object(intake.subprocess, 'run', return_value=SimpleNamespace(
                    returncode=rc, stdout=stdout, stderr=stderr)):
                with self.assertRaises(intake.ReadError) as error:
                    intake.read_device('LAB-08B', 'pin\n', 'ephemeral')
                self.assertEqual(error.exception.code, code)
                self.assertNotIn('secret', str(error.exception))

    def test_raw_command_pins_key_uses_memory_password_and_never_becomes_root(self):
        def subprocess_run(command, **kwargs):
            fd = kwargs['pass_fds'][0]
            self.assertEqual(os.pread(fd, 256, 0), b'ephemeral')
            self.assertNotIn('ephemeral', str(command) + str(kwargs.get('env')))
            settings = json.loads(command[command.index('-e')+1])
            self.assertIs(settings['ansible_become'], False)
            self.assertIn('StrictHostKeyChecking=yes', settings['ansible_ssh_common_args'])
            self.assertIn('LAB-08B', command)
            self.assertEqual(command[3], 'adhoc')
            self.assertNotIn('--connection-password-file', command)
            self.assertEqual(command[command.index('-m')+1], 'raw')
            self.assertIn('-IB', command[command.index('-a')+1])
            return SimpleNamespace(returncode=0, stdout='LAB-08B | CHANGED | rc=0 >>\n'+intake.MARKER+
                                   json.dumps(dict(ok=True, device=device())), stderr='')
        with patch.object(intake.subprocess, 'run', side_effect=subprocess_run):
            self.assertEqual(intake.read_device('LAB-08B', 'pin\n', 'ephemeral'), device())

    def test_keyscan_rejects_ambiguous_or_invalid_keys(self):
        blob = b'\0\0\0\x0bssh-ed25519\0\0\0\x20' + bytes(range(32))
        encoded = base64.b64encode(blob).decode()
        with patch.object(intake.subprocess, 'run', return_value=SimpleNamespace(
                returncode=0, stdout=f'203.0.113.16 ssh-ed25519 {encoded}\n')):
            key, fingerprint = intake.scan_key('203.0.113.16')
            self.assertEqual(fingerprint, 'SHA256:'+base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip('='))
            self.assertIn(encoded, key)
        with patch.object(intake.subprocess, 'run', return_value=SimpleNamespace(
                returncode=0, stdout='203.0.113.16 ssh-ed25519 YQ==\n')):
            with self.assertRaises(ValueError): intake.scan_key('203.0.113.16')

    def test_b_pilot_gate_and_identity_change(self):
        with self.assertRaises(ValueError): self.queue.check_intake_pilot(['LAB-01A'])
        with self.assertRaises(ValueError): self.queue.check_intake_pilot(['LAB-08B', 'LAB-01A'])
        self.queue.check_intake_pilot(['LAB-08B'])
        with self.assertRaises(ValueError): self.intake.accept_pilot('unknown', True)
        read = self.completed_read(); read['pin'] = 'pin'; self.intake.save(read)
        run = dict(id='c'*32, admission_id='d'*32, host='LAB-08B', status='succeeded',
                   phase='maintenance', started=self.queue.now(), pin_sha256=hashlib.sha256(b'pin').hexdigest())
        self.queue.save(run)
        for name in ('LAB-08B.'+'b'*32+'.known_hosts', 'LAB-08B.active.known_hosts'):
            (self.admissions / name).write_text('pin')
        self.intake.prepare(read['id'], 'enx001122334466', True)
        data = admission.read_settings(self.hardware / 'LAB-08B.json')
        data['lab_fresh_known_hosts'] = str(self.admissions / ('LAB-08B.'+'b'*32+'.known_hosts'))
        admission.write_private(self.admissions / ('LAB-08B.'+'d'*32+'.used'), json.dumps(data))
        self.assertTrue(self.intake.accept_pilot(run['id'], True)['accepted'])
        self.queue.check_intake_pilot(['LAB-01A'])
        (self.admissions / 'LAB-08B.active.known_hosts').write_text('different')
        self.assertFalse(self.intake.pilot_accepted())

    def test_failed_newer_b_run_cannot_be_accepted_via_old_success(self):
        self.completed_read()
        self.queue.save(dict(id='c'*32, host='LAB-08B', status='succeeded', phase='maintenance',
                             started=self.queue.now(), pin_sha256='test'))
        self.queue.save(dict(id='d'*32, host='LAB-08B', status='failed', phase='basis', started=self.queue.now()))
        with self.assertRaises(ValueError): self.intake.accept_pilot('c'*32, True)

    def test_state_is_local_and_does_not_expose_pins(self):
        self.completed_read()
        with patch.object(intake, 'scan_key') as scan, patch.object(intake, 'read_device') as read:
            state = self.intake.state()
            self.assertEqual(len(state['clients']), 16)
            self.assertNotIn('pin', state['reads'][0])
            scan.assert_not_called(); read.assert_not_called()

    def test_complete_backend_intake_uses_real_bootstrap_and_baseline_record_links(self):
        def wait_for_unlock():
            deadline = monotonic() + 3
            while self.deploys.lock.locked():
                self.assertLess(monotonic(), deadline); sleep(.005)
        self.deploys.appearance = SimpleNamespace(active=lambda: {key: ('fixture', self.root / 'image.jpg')
                                                                 for key in ('desktop', 'lock')})
        self.deploys.command = lambda *args, **kwargs: ['ansible-playbook', '-e', '{}', 'local-cisco.yml']
        with patch.object(intake, 'scan_key', return_value=('fixture pin\n', 'SHA256:'+'a'*43)), \
                patch.object(intake, 'read_device', return_value=device()), \
                patch.object(admission, 'verified_key', return_value='fixture pin\n'), \
                patch.object(admission.Bootstrap, 'run_ansible', return_value=0), \
                patch.object(self.queue, 'validate_fresh_inputs'), \
                patch.object(self.queue, 'run_playbook', return_value=0):
            read = self.intake.start('LAB-08B', 'ephemeral', 'student', True); wait_for_unlock()
            bootstrap = self.intake.setup(read['id'], 'enx001122334466', 'ephemeral', 'ephemeral', True)
            wait_for_unlock()
            fresh = self.queue.fresh_start([dict(host='LAB-08B', veyon=False)], True)['runs'][0]
            wait_for_unlock()
            self.assertNotEqual(bootstrap['id'], fresh['admission_id'])
            self.assertTrue(self.intake.accept_pilot(fresh['id'], True)['accepted'])
            self.assertTrue(self.intake.pilot_accepted())


class TeacherRoomTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(TemporaryDirectory()))
        self.deploys = SimpleNamespace(lock=threading.Lock())
        self.room = room.RoomActions(self.root, self.deploys)

    def test_app_room_rejects_unenrolled_power_targets_before_lock_and_only_offers_wake(self):
        self.room.profile='apps'
        pins=self.root/'fresh';pins.mkdir()
        with patch.object(room,'ADMISSIONS',pins),patch('maintenance.controller.targets',return_value={'LAB-08A':{}}):
            access,capabilities=self.room.capabilities()
            self.assertTrue(access['LAB-08A']);self.assertFalse(access['LAB-03A'])
            self.assertIn('poweroff',capabilities['LAB-08A']);self.assertEqual(capabilities['LAB-03A'],['wake'])
            with self.assertRaisesRegex(ValueError,'LAB-03A'):
                self.room.start('poweroff',confirmed=True,targets=['LAB-08A','LAB-03A'])
        self.assertFalse(self.deploys.lock.locked());self.assertFalse(self.room.history())

    def test_room_persists_partial_and_failed_outcome_instead_of_plain_finished(self):
        for results,status in ((['Fehler: Test','Übermittelt'],'partial'),(['Fehler: Test']*2,'failed')):
            with patch.object(self.room,'on_host',side_effect=results):
                with patch.object(threading,'Thread'):
                    run=self.room.start('poweroff',confirmed=True,targets=['LAB-01A','LAB-01B'])
                self.room.execute(run,None)
            self.assertEqual(run['status'],status)

    def test_custom_selection_runs_and_persists_exactly_the_chosen_clients(self):
        selected = ['LAB-05B', 'LAB-03A', 'LAB-05A', 'LAB-04B']
        expected = ['LAB-03A', 'LAB-04B', 'LAB-05A', 'LAB-05B']
        with patch.object(self.room, 'on_host', return_value='Fixture') as action:
            with patch.object(threading, 'Thread'):
                run = self.room.start('poweroff', confirmed=True, targets=selected)
            selected.append('LAB-01A')
            self.room.execute(run, None)
        self.assertEqual({call.args for call in action.call_args_list}, {(host, 'poweroff', None) for host in expected})
        self.assertEqual(run['targets'], expected)
        self.assertEqual(self.room.history()[0]['targets'], expected)
        self.assertEqual(set(run['results']), set(expected))
        self.assertFalse(self.deploys.lock.locked())

    def test_invalid_custom_selection_has_no_lock_or_action_side_effect(self):
        for targets in (None, [], 'LAB-03A', ['203.0.113.5'], ['iserv8'], ['LAB-99A'],
                        ['LAB-03A'] * 2, ['LAB-03A'] * 18, [3], [['LAB-03A']], ('LAB-03A',)):
            with self.subTest(targets=targets), self.assertRaises(ValueError):
                self.room.start('wake', confirmed=True, targets=targets)
            self.assertFalse(self.deploys.lock.locked())
        with self.assertRaises(ValueError):
            self.room.start('wake', scope='room', confirmed=True, targets=['LAB-03A'])
        for action, confirmed in (([], True), ('shell', True), ('wake', False), ('wake', 1)):
            with self.subTest(action=action, confirmed=confirmed), self.assertRaises(ValueError):
                self.room.start(action, confirmed=confirmed, targets=['LAB-03A'])
        self.assertEqual(self.room.history(), [])

    def test_custom_teacher_selection_rejects_unsafe_actions_before_start(self):
        for action in set(room.ACTIONS) - {'wake', 'poweroff'}:
            with self.subTest(action=action), self.assertRaises(ValueError):
                self.room.start(action, confirmed=True, targets=['LAB-03A', 'LAB-Teacher'])
            self.assertFalse(self.deploys.lock.locked())

    def test_custom_wake_sends_only_selected_student_and_teacher_packets(self):
        route = ('203.0.113.200', '203.0.113.255')
        with patch.object(room.RoomActions, 'wake_route', return_value=route), \
                patch.object(room.RoomActions, 'macs', return_value={'LAB-03A':'02:11:22:33:44:55'}), \
                patch.object(teacher_wake, 'configuration', return_value={'school_mac':'02:00:00:00:00:64'}), \
                patch.object(teacher_wake, 'route', return_value=route), \
                patch.object(room.RoomActions, 'wake', return_value='Fixture') as packet:
            with patch.object(threading, 'Thread') as worker:
                run = self.room.start('wake', confirmed=True, targets=['LAB-03A', 'LAB-Teacher'])
                context = worker.call_args.kwargs['args'][1]
            self.room.execute(run, context)
        self.assertEqual({call.args for call in packet.call_args_list},
                         {('02:11:22:33:44:55', route), ('02:00:00:00:00:64', route)})
        self.assertEqual(set(run['results']), {'LAB-03A', 'LAB-Teacher'})

    def test_teacher_poweroff_uses_only_closed_receiver_command_and_reports_refusal(self):
        state={'boot_id':'c9c05f21-3049-4184-b85b-5b5c5d433649','capabilities':['manual-poweroff']}
        with patch('maintenance.teacher_controller.probe',return_value=state), \
                patch('maintenance.teacher_controller.request') as request:
            request.side_effect=lambda command:{'poweroff_requested':True,'id':command.split()[1]}
            result=self.room.on_host('LAB-Teacher','poweroff',None)
        command=request.call_args.args[0].split()
        self.assertEqual(command[0],'manual-poweroff')
        self.assertRegex(command[1],r'^[a-f0-9]{32}$')
        self.assertEqual(command[2],state['boot_id'])
        self.assertIn('Herunterfahren eingeplant',result)
        with patch('maintenance.teacher_controller.probe',side_effect=RuntimeError('lokale Sitzung')):
            self.assertEqual(self.room.on_host('LAB-Teacher','poweroff',None),'Fehler: lokale Sitzung')
        with patch('maintenance.teacher_controller.probe',return_value={'boot_id':state['boot_id']}), \
                patch('maintenance.teacher_controller.request') as request:
            self.assertEqual(self.room.on_host('LAB-Teacher','poweroff',None),
                             'Fehler: Teacher-Empfänger zuerst aktualisieren.')
            request.assert_not_called()

    def test_teacher_poweroff_start_does_not_require_student_admission(self):
        self.room.profile='apps'
        with patch.object(self.room,'access',return_value={host:False for host in room.TARGETS}), \
                patch.object(threading,'Thread'):
            run=self.room.start('poweroff',confirmed=True,targets=['LAB-Teacher'])
        self.assertEqual(run['targets'],['LAB-Teacher'])
        self.deploys.lock.release()

    def test_teacher_unsafe_actions_fail_before_any_student_action(self):
        for scope in ('teacher', 'room-with-teacher'):
            for action in set(room.ACTIONS) - {'wake', 'poweroff'}:
                with self.subTest(scope=scope, action=action), self.assertRaises(ValueError):
                    self.room.start(action, scope, True)
                self.assertFalse(self.deploys.lock.locked())

    def test_teacher_wake_uses_confirmed_mac_without_student_inventory(self):
        route = ('203.0.113.200', '203.0.113.255')
        with patch.object(room.RoomActions, 'wake_route', return_value=route), \
                patch.object(room.RoomActions, 'macs') as macs, \
                patch.object(teacher_wake, 'configuration', return_value={'school_mac': '02:00:00:00:00:64'}), \
                patch.object(teacher_wake, 'route', return_value=route), \
                patch.object(threading, 'Thread') as thread:
            run = self.room.start('wake', 'teacher', True)
            self.assertEqual(run['scope'], 'teacher')
            macs.assert_not_called()
            context = thread.call_args.kwargs['args'][1]
            self.assertEqual(context, ({'LAB-Teacher': '02:00:00:00:00:64'}, route))
            self.deploys.lock.release()

    def test_combined_wake_keeps_exact_student_inventory(self):
        route = ('203.0.113.200', '203.0.113.255')
        inventory = {host: '02:11:22:33:44:55' for host in room.TARGETS}
        with patch.object(room.RoomActions, 'wake_route', return_value=route), \
                patch.object(room.RoomActions, 'macs', return_value=inventory), \
                patch.object(teacher_wake, 'configuration', return_value={'school_mac': '02:00:00:00:00:64'}), \
                patch.object(teacher_wake, 'route', return_value=route), patch.object(threading, 'Thread') as thread:
            self.room.start('wake', 'room-with-teacher', True)
            self.assertEqual(len(room.TARGETS), 16)
            self.assertEqual(len(thread.call_args.kwargs['args'][1][0]), 17)
            self.deploys.lock.release()


class CollectorTests(unittest.TestCase):
    def test_exercise_ranges_stay_available_without_a_matching_school_route(self):
        protected=collect_device.protected_networks([
            {'dst':'default'}, {'dst':'203.0.113.0/24'},
            {'dst':'fe80::/64'}, {'dst':'169.254.0.0/16'}, {'dst':'::/0'}])
        self.assertEqual(protected,['203.0.113.0/24'])
        identity=device()['adapters']
        from network.security.lab_request import validate_request
        manifest={'school':{key:value for key,value in identity[0].items() if key!='school'},
                  'lab':{key:value for key,value in identity[1].items() if key!='school'},
                  'protected_subnets':protected}
        self.assertEqual(validate_request({'action':'apply','addresses':['198.51.100.6/29'],'routes':[]},
                                         manifest)['addresses'],['198.51.100.6/29'])

    def test_actual_static_school_routes_are_still_protected(self):
        protected=collect_device.protected_networks([
            {'dst':'192.168.0.0/24'}, {'dst':'192.0.2.0/24'}, {'dst':'2001:db8:123::/64'}])
        self.assertEqual(protected,sorted(['203.0.113.0/24','192.0.2.0/24','192.168.0.0/24','2001:db8:123::/64']))

    def test_actual_profile_and_usb_identity_are_collected_without_writes(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            net = root / 'sys/class/net'; net.mkdir(parents=True)
            internal = root / 'devices/pci/eno1'; usb = root / 'devices/pci/usb1/enx001122334466'
            for path, mac in ((internal, '02:11:22:33:44:55'), (usb, '02:11:22:33:44:66')):
                path.mkdir(parents=True); (path / 'device').mkdir()
                (path / 'type').write_text('1'); (path / 'address').write_text(mac)
                (net / path.name).symlink_to(path)
            (root / 'etc').mkdir(); (root / 'etc/os-release').write_text('ID=debian\nVERSION_ID="13"\n')
            school_uuid = '11111111-1111-1111-1111-111111111111'
            def command(*args):
                if args[0] == '/usr/bin/nmcli':
                    return school_uuid+':eno1' if '--active' in args else school_uuid
                if 'address' in args: return json.dumps([dict(ifname='eno1', addr_info=[dict(local='203.0.113.16')])])
                if args[-1] == 'default': return json.dumps([dict(dst='default', dev='eno1')])
                return json.dumps([dict(dst='fe80::/64' if '-6' in args else '203.0.113.0/24')])
            real_path = Path
            with patch.object(collect_device, 'Path', side_effect=lambda value: real_path(root / value.lstrip('/'))), \
                    patch.object(collect_device.socket, 'gethostname', return_value='LAB-08B'), \
                    patch.object(collect_device, 'command', side_effect=command), \
                    patch.object(collect_device.subprocess, 'check_output', return_value='eno1\n02:11:22:33:44:55\n'):
                result = collect_device.collect('LAB-08B', '203.0.113.16')
            intake.validate_device('LAB-08B', result)
            self.assertEqual(result['adapters'][0]['uuid'], school_uuid)
            self.assertTrue(result['adapters'][1]['usb'])
            self.assertEqual(result['protected'],['203.0.113.0/24'])



if __name__ == '__main__':
    unittest.main()
