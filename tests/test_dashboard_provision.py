"""Regression tests for the fresh-client queue without SSH or client changes."""
import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import threading
from time import monotonic, sleep
import unittest
from unittest.mock import patch
import yaml

from dashboard import admission, deploy, provision


class Deployments:
    def __init__(self):
        self.lock = threading.Lock()
        self.appearance = self

    def active(self):
        return {name: ('test', Path(f'/test/{name}.jpg')) for name in ('desktop', 'lock')}

    @staticmethod
    def command(host, job, **settings):
        return ['ansible-playbook', '--limit', host, '-e', '{}', 'local-cisco.yml']

    @staticmethod
    def validate_id(run_id):
        if len(run_id) != 32 or any(c not in '0123456789abcdef' for c in run_id):
            raise FileNotFoundError


class QueueTests(unittest.TestCase):
    def test_named_group_merges_software_once_and_keeps_the_three_baseline_phases(self):
        host = 'LAB-01A'
        self.prepare(host)
        group = dict(id='d'*32, name='Netzwerkkurs', steps=[
            dict(id='serial', label='Konsole', in_baseline=True),
            dict(id='software:'+'e'*32, label='Werkzeuge', packages=['filezilla','gimp'])])
        from types import SimpleNamespace
        self.queue.playbook_groups = SimpleNamespace(fresh_selection=lambda identity: group)
        with patch('dashboard.software.baseline_packages', return_value=['filezilla']), \
                patch.object(provision,'packettracer_eula_version',return_value=''), \
                patch.object(provision,'packettracer_defaults',return_value=''), \
                patch.object(self.queue,'run_playbook',return_value=0) as playbook:
            self.queue.fresh_start([dict(host=host,veyon=False)], True, group['id'])
            self.until(lambda:not self.deployments.lock.locked())
        self.assertEqual([call.args[0][-1] for call in playbook.call_args_list],
                         ['fresh-client.yml','local-cisco.yml','maintenance-client.yml'])
        self.assertEqual(json.loads(playbook.call_args_list[0].args[0][-2])['lab_software_packages'],['filezilla','gimp'])
        self.assertEqual(self.queue.records()[0]['playbook_group']['name'],'Netzwerkkurs')

    def test_packettracer_approval_requires_explicit_supported_operator_file(self):
        with TemporaryDirectory() as temporary:
            root=Path(temporary)
            self.assertEqual(provision.packettracer_eula_version(root),'')
            directory=root/'local-private'
            directory.mkdir()
            (directory/'packettracer-eula-version').write_text('9.0\n')
            self.assertEqual(provision.packettracer_eula_version(root),'9.0')
            (directory/'packettracer-eula-version').write_text('9.1\n')
            with self.assertRaises(ValueError): provision.packettracer_eula_version(root)

    def test_operator_packettracer_approval_reaches_fresh_installation(self):
        self.prepare('LAB-01A')
        with patch.object(provision,'packettracer_eula_version',return_value='9.0'), \
                patch.object(provision,'packettracer_defaults',return_value='/private/neutral.defaults'), \
                patch.object(self.queue,'run_playbook',return_value=0) as playbook:
            self.queue.fresh_start([dict(host='LAB-01A',veyon=False)],True)
            self.until(lambda:not self.deployments.lock.locked())
        settings=json.loads(playbook.call_args_list[0].args[0][-2])
        self.assertEqual(settings['lab_fresh_packettracer_eula_version'],'9.0')
        self.assertEqual(settings['lab_fresh_packettracer_defaults'],'/private/neutral.defaults')

    def test_neutral_packettracer_preferences_are_optional_and_bounded(self):
        with TemporaryDirectory() as temporary:
            root=Path(temporary)
            self.assertEqual(provision.packettracer_defaults(root),'')
            path=root/'local-private/packettracer-9.0.defaults';path.parent.mkdir()
            path.write_bytes(b'neutral')
            self.assertEqual(provision.packettracer_defaults(root),str(path))
            path.write_bytes(b'x'*65537)
            with self.assertRaises(ValueError): provision.packettracer_defaults(root)

    def setUp(self):
        directory = self.enterContext(TemporaryDirectory())
        root = Path(directory)
        self.admissions = root / 'admissions'
        self.admissions.mkdir()
        self.known = root / 'known_hosts'
        self.known.write_text('test pin')
        self.enterContext(patch.object(provision, 'ADMISSIONS', self.admissions))
        self.enterContext(patch.object(admission, 'ADMISSIONS', self.admissions))
        self.enterContext(patch.object(admission, 'HARDWARE', root / 'hardware'))
        self.enterContext(patch.object(provision, 'veyon_available', return_value=True))
        self.enterContext(patch.object(provision.Provision, 'validate_fresh_inputs'))
        self.deployments = Deployments()
        self.queue = provision.Provision(root / 'runs', self.deployments)

    def prepare(self, host):
        data = dict(lab_fresh_confirmed=True, lab_fresh_host=host, lab_fresh_student='student',
                    lab_fresh_packettracer_deb='/test/packettracer.deb', lab_fresh_known_hosts=str(self.known),
                    lab_network_manifest=dict(version=1, host=host, group='cisco.lab', identity={
                        'school': dict(interface='eno1', mac='01:02:03:04:05:06', usb=False,
                                       uuid='11111111-1111-1111-1111-111111111111'),
                        'lab': dict(interface='lab0', mac='02:02:03:04:05:06', usb=True,
                                     uuid='22222222-2222-2222-2222-222222222222'),
                        'protected_subnets': ['203.0.113.0/24'],
                    }))
        path = self.admissions / (host + '.json')
        admission.write_private(path, json.dumps(data))
        return path

    def until(self, condition):
        deadline = monotonic() + 3
        while not condition():
            self.assertLess(monotonic(), deadline)
            sleep(.01)

    def test_worker_capacity_and_queued_stop_preserves_admission(self):
        hosts = ('LAB-01A', 'LAB-01B', 'LAB-02A', 'LAB-02B', 'LAB-03A')
        for host in hosts:
            self.prepare(host)
        release = threading.Event()
        started = []
        settings = {}

        def playbook(command, log):
            if command[-1] == 'fresh-client.yml':
                host = command[command.index('--limit') + 1]
                settings[host] = json.loads(command[-2])
                started.append(host)
                log.write('TASK [Testaufgabe] ********\n')
                release.wait(5)
            return 0

        # Keep one target queued without starting sixteen mock installations.
        with patch.object(provision, 'FRESH_WORKERS', 4), \
                patch.object(self.queue, 'run_playbook', side_effect=playbook):
            try:
                result = self.queue.fresh_start([dict(host=host, veyon=host == hosts[0]) for host in hosts], True)
                self.until(lambda: len(started) == 4)
                self.assertCountEqual(started, hosts[:4])
                self.assertIs(settings[hosts[0]]['lab_fresh_install_veyon'], True)
                self.assertIs(settings[hosts[1]]['lab_fresh_install_veyon'], False)
                log_path = self.queue.directory / (result['runs'][0]['id'] + '.log')
                os.utime(log_path, (1, 1))
                first = next(run for run in self.queue.fresh_state()['runs'] if run['host'] == hosts[0])
                self.assertTrue(first['quiet'])
                self.assertIn('Testaufgabe', first['task'])
                self.queue.stop(result['runs'][4]['id'], True)
            finally:
                release.set()
                self.until(lambda: not self.deployments.lock.locked())
        self.assertEqual({r['host']: r['status'] for r in self.queue.records()},
                         dict(zip(hosts, ('succeeded', 'succeeded', 'succeeded', 'succeeded', 'stopped'))))
        self.assertTrue((self.admissions / (hosts[4] + '.json')).exists())

    def test_stop_after_basis_and_retry_uses_only_cisco_phase(self):
        host = 'LAB-01A'
        self.prepare(host)
        release = threading.Event()
        running = threading.Event()

        def playbook(command, log):
            self.assertEqual(command[-1], 'fresh-client.yml')
            running.set()
            release.wait(5)
            return 0

        with patch.object(self.queue, 'run_playbook', side_effect=playbook):
            try:
                result = self.queue.fresh_start([dict(host=host, veyon=False)], True)
                run_id = result['runs'][0]['id']
                self.assertTrue(running.wait(3))
                self.queue.stop(run_id, True)
            finally:
                release.set()
                self.until(lambda: not self.deployments.lock.locked())
        stopped = self.queue.fresh_state()['runs'][0]
        self.assertEqual((stopped['status'], stopped['phase'], stopped['retryable']), ('stopped', 'cisco', True))
        self.known.write_text('replacement key')
        with self.assertRaises(ValueError):
            self.queue.retry(run_id, True)
        self.known.write_text('test pin')
        with patch.object(self.queue, 'run_playbook', return_value=0) as playbook:
            retry = self.queue.retry(run_id, True)
            self.until(lambda: not self.deployments.lock.locked())
            self.assertEqual(playbook.call_count, 2)
            command = playbook.call_args_list[0].args[0]
            self.assertEqual(command[-1], 'local-cisco.yml')
            self.assertEqual(playbook.call_args_list[1].args[0][-1], 'maintenance-client.yml')
            self.assertIn(str(self.known), json.loads(command[-2])['ansible_ssh_common_args'])
        self.assertEqual(retry['runs'][0]['resumed_from'], run_id)
        self.assertEqual(self.queue.records()[0]['status'], 'succeeded')

    def test_rejects_entire_batch_if_a_target_is_unprepared(self):
        host = 'LAB-01A'
        pending = self.prepare(host)
        with self.assertRaises(FileNotFoundError):
            self.queue.fresh_start([dict(host=host, veyon=False), dict(host='LAB-01B', veyon=False)], True)
        self.assertTrue(pending.exists())
        self.assertEqual(self.queue.records(), [])
        self.assertFalse(self.deployments.lock.locked())

    def test_approved_software_selection_is_snapshotted_for_baseline(self):
        self.prepare('LAB-01A')
        with patch('dashboard.software.baseline_packages',return_value=['gimp','vlc']),patch.object(self.queue,'run_playbook',return_value=0) as playbook:
            self.queue.fresh_start([dict(host='LAB-01A',veyon=False)],True)
            self.until(lambda:not self.deployments.lock.locked())
        self.assertEqual(self.queue.records()[0]['software_packages'],['gimp','vlc'])
        settings=json.loads(playbook.call_args_list[0].args[0][-2])
        self.assertEqual(settings['lab_software_packages'],['gimp','vlc'])

    def test_maintenance_retry_does_not_repeat_successful_account_or_basis(self):
        host = 'LAB-01A'
        self.prepare(host)
        def fail_maintenance(command, log):
            return 1 if command[-1] == 'maintenance-client.yml' else 0
        with patch.object(self.queue, 'run_playbook', side_effect=fail_maintenance):
            result = self.queue.fresh_start([dict(host=host, veyon=False)], True)
            self.until(lambda: not self.deployments.lock.locked())
        old = self.queue.records()[0]
        self.assertEqual((old['status'], old['phase']), ('failed', 'maintenance'))
        with patch.object(self.queue, 'run_playbook', return_value=0) as playbook:
            self.queue.retry(result['runs'][0]['id'], True)
            self.until(lambda: not self.deployments.lock.locked())
        playbook.assert_called_once()
        self.assertEqual(playbook.call_args.args[0][-1], 'maintenance-client.yml')
        self.assertNotIn('lab_maintenance_enable_timer', json.loads(playbook.call_args.args[0][-2]))

    def test_fresh_baseline_removes_local_client_timer(self):
        root = provision.ROOT
        baseline = yaml.safe_load((root / 'lab-baseline.yml').read_text())
        maintenance = yaml.safe_load((root / 'maintenance-client.yml').read_text())[0]
        self.assertNotIn('lab_maintenance_enable_timer', baseline[-1]['vars'])
        self.assertNotIn('lab_maintenance_enable_timer', maintenance['vars'])
        self.assertFalse((root / 'maintenance/systemd/cisco-lab-maintenance.timer').exists())
        removals = [task for task in maintenance['tasks']
                    if task.get('ansible.builtin.file', {}).get('path')
                    == '/etc/systemd/system/cisco-lab-maintenance.timer']
        self.assertEqual(len(removals), 1)
        self.assertEqual(removals[0]['ansible.builtin.file']['state'], 'absent')

    def test_unregistered_old_b_pilot_cannot_start_maintenance(self):
        with patch('maintenance.controller.targets', return_value={'LAB-08A': {}}):
            self.assertEqual(self.queue.maintenance_hosts(), ['LAB-08A'])
            with self.assertRaisesRegex(ValueError, 'nicht für die zentrale Wartung registriert'):
                self.queue.maintenance_state('LAB-08B')
            with self.assertRaisesRegex(ValueError, 'nicht für die zentrale Wartung registriert'):
                self.queue.maintenance_start(True, 'LAB-08B')

    def test_gui_maintenance_holds_shared_lock_until_controller_finishes(self):
        from maintenance import controller
        running = threading.Event()
        release = threading.Event()

        def execute_locked(enrolled, manual):
            self.assertEqual(enrolled, {'LAB-01A': {'mac': 'test'}})
            self.assertTrue(manual)
            running.set()
            release.wait(3)

        with (patch.object(controller, 'targets', return_value={'LAB-01A': {'mac': 'test'}}),
              patch.object(controller, 'execute_locked', side_effect=execute_locked)):
            try:
                self.assertEqual(self.queue.maintenance_start(True, 'LAB-01A'), {'started': True})
                self.assertTrue(running.wait(3))
                self.assertTrue(self.deployments.lock.locked())
                self.assertIsNone(self.queue.maintenance_start(True, 'LAB-01A'))
            finally:
                release.set()
                self.until(lambda: not self.deployments.lock.locked())

    def test_manual_maintenance_accepts_selected_clients_outside_evening_window(self):
        from maintenance import controller
        enrolled = {'LAB-01A': {'mac': 'a'}, 'LAB-03B': {'mac': 'b'}, 'LAB-08A': {'mac': 'c'}}
        with (patch.object(controller, 'targets', return_value=enrolled),
              patch.object(controller, 'execute_locked') as execute,
              patch.object(controller, 'start_allowed', return_value=False)):
            self.assertEqual(self.queue.maintenance_start(True, hosts=['LAB-03B', 'LAB-01A']), {'started': True})
            self.until(lambda: not self.deployments.lock.locked())
        execute.assert_called_once_with({host: enrolled[host] for host in ['LAB-01A', 'LAB-03B']}, True)

    def test_manual_maintenance_rejects_empty_duplicate_foreign_and_unconfirmed_selection(self):
        from maintenance import controller
        with patch.object(controller, 'targets', return_value={'LAB-01A': {}}), \
                patch.object(controller, 'execute') as execute:
            for hosts in ([], ['LAB-01A', 'LAB-01A'], ['LAB-Teacher'], ['LAB-02A'], 'LAB-01A', [None]):
                with self.subTest(hosts=hosts), self.assertRaises(ValueError):
                    self.queue.maintenance_start(True, hosts=hosts)
            with self.assertRaisesRegex(ValueError, 'ausdrücklich bestätigen'):
                self.queue.maintenance_start(False, hosts=['LAB-01A'])
            execute.assert_not_called()
            self.assertFalse(self.deployments.lock.locked())

    def test_manual_status_no_longer_requires_evening_window(self):
        from maintenance import controller
        with patch.object(controller, 'targets', return_value={'LAB-01A': {'known_hosts': '/fixture/pin'}}), \
                patch.object(provision.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, 'loaded\ninactive\nstatus', '')):
            self.assertTrue(self.queue.maintenance_state('LAB-01A')['can_start'])
            self.deployments.lock.acquire()
            try:
                self.assertFalse(self.queue.maintenance_state('LAB-01A')['can_start'])
            finally:
                self.deployments.lock.release()


class BootstrapTests(unittest.TestCase):
    def test_apps_bootstrap_uses_apps_source_and_builtin_askpass(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            hardware = root / 'hardware'
            hardware.mkdir()
            admissions = root / 'admissions'
            admissions.mkdir()
            host = 'LAB-01A'
            data = dict(lab_fresh_confirmed=True, lab_fresh_host=host,
                        lab_fresh_student='student',
                        lab_fresh_packettracer_deb='/test/packettracer.deb',
                        lab_network_manifest=dict(version=1, host=host, group='cisco.lab',
                                                  identity=dict(
                            school=dict(interface='eno1', mac='01:02:03:04:05:06', usb=False,
                                        uuid='11111111-1111-1111-1111-111111111111'),
                            lab=dict(interface='lab0', mac='02:02:03:04:05:06', usb=True,
                                     uuid='22222222-2222-2222-2222-222222222222'),
                            protected_subnets=['203.0.113.0/24'])))
            admission.write_private(hardware / (host + '.json'), json.dumps(data))
            captured = []
            lock = threading.Lock()
            lock.acquire()
            app = admission.Bootstrap(root, lock, 'apps')
            with (patch.object(admission, 'HARDWARE', hardware),
                  patch.object(admission, 'ADMISSIONS', admissions),
                  patch.object(app, 'run_ansible', side_effect=lambda _host, settings, _pw:
                               captured.append(settings) or 0)):
                app.run(dict(id='a' * 32, host=host), data, '203.0.113.1 ssh-ed25519 test\n',
                        ('ssh', 'sudo'), lambda _run: None, lambda: 'now')
            self.assertEqual(captured[0]['lab_bootstrap_controller_ip'], '203.0.113.200')
            self.assertEqual(captured[0]['ansible_ssh_password_mechanism'], 'ssh_askpass')
            self.assertEqual(admission.Bootstrap(root, threading.Lock()).controller_ip, '203.0.113.100')
            play = yaml.safe_load((admission.ROOT / 'bootstrap-client.yml').read_text())[0]
            key_task = next(task for item in play['tasks'] for task in item.get('block', [])
                            if 'ansible.posix.authorized_key' in task)
            self.assertEqual(key_task['ansible.posix.authorized_key']['key_options'],
                             'restrict,from="{{ bootstrap_controller_ip }}"')

    def test_keyscan_requires_expected_fingerprint_and_address(self):
        blob = b'fake-key'
        line = '203.0.113.1 ssh-ed25519 ' + base64.b64encode(blob).decode()
        fingerprint = 'SHA256:' + base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip('=')
        with patch.object(admission.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, line)):
            self.assertEqual(admission.verified_key('203.0.113.1', fingerprint), line + '\n')
            with self.assertRaises(ValueError):
                admission.verified_key('203.0.113.2', fingerprint)
            with self.assertRaises(ValueError):
                admission.verified_key('203.0.113.1', 'SHA256:' + 'a' * 43)

    def test_passwords_are_only_passed_as_anonymous_files(self):
        passwords = ('ssh test password', 'sudo test password')
        original_run = subprocess.run

        def run(command, **kwargs):
            self.assertFalse(any(password in ' '.join(command) for password in passwords))
            self.assertEqual(command[3], 'playbook')
            paths = ['/proc/self/fd/' + str(fd) for fd in kwargs['pass_fds']]
            for fd in kwargs['pass_fds']:
                self.assertEqual(os.fstat(fd).st_mode & 0o777, 0o600)
            code = 'from ansible.cli import CLI; import sys; print(*map(CLI.get_password_from_file, sys.argv[1:]), sep="\\n")'
            process = original_run([sys.executable, '-B', '-c', code, *paths], text=True,
                                   capture_output=True, pass_fds=kwargs['pass_fds'], check=True)
            self.assertEqual(process.stdout.splitlines(), list(passwords))
            return process

        with patch.object(admission.subprocess, 'run', side_effect=run):
            self.assertEqual(admission.Bootstrap.run_ansible('LAB-01A', {}, passwords), 0)


class PilotReviewTests(unittest.TestCase):
    def test_only_latest_success_on_each_host_can_be_reviewed(self):
        directory = self.enterContext(TemporaryDirectory())
        deployments = deploy.Deployments(Path(directory), None)
        jobs = [
            ('a', 'LAB-08A', 'succeeded', 'current'),
            ('b', 'LAB-08A', 'succeeded', 'current'),
            ('c', 'LAB-08A', 'failed', 'current'),
            ('d', 'LAB-08B', 'succeeded', 'old'),
        ]
        for offset, (run_id, host, status, revision) in enumerate(jobs):
            deployments.save(dict(id=run_id * 32, host=host, job='firefox', status=status,
                                  revision=revision, started=f'2026-11-06T21:00:0{offset}+01:00'))
        with patch.object(deployments, 'revision', return_value='current'):
            runs = {run['id'][0]: run for run in deployments.reviewable_history()}
            self.assertTrue(runs['a']['superseded'])
            self.assertFalse(runs['b']['superseded'])
            self.assertTrue(runs['d']['source_changed'])
            checks = list(deploy.CHECKS['firefox'])
            for run_id in ('a', 'd'):
                with self.assertRaises(ValueError):
                    deployments.review(run_id * 32, 'accepted', checks, '')
            review = deployments.review('b' * 32, 'accepted', checks, '')
            self.assertEqual(review['review']['decision'], 'accepted')
