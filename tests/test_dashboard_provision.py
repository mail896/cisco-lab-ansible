"""Regression tests for the fresh-client queue without SSH or client changes."""
import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import threading
from time import monotonic, sleep
import unittest
from unittest.mock import patch

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
                        'protected_subnets': ['10.255.0.0/16'],
                    }))
        path = self.admissions / (host + '.json')
        admission.write_private(path, json.dumps(data))
        return path

    def until(self, condition):
        deadline = monotonic() + 3
        while not condition():
            self.assertLess(monotonic(), deadline)
            sleep(.01)

    def test_two_workers_and_queued_stop_preserves_admission(self):
        hosts = ('LAB-01A', 'LAB-01B', 'LAB-02A')
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

        with patch.object(self.queue, 'run_playbook', side_effect=playbook):
            try:
                result = self.queue.fresh_start([dict(host=host, veyon=host == hosts[0]) for host in hosts], True)
                self.until(lambda: len(started) == 2)
                self.assertCountEqual(started, hosts[:2])
                self.assertIs(settings[hosts[0]]['lab_fresh_install_veyon'], True)
                self.assertIs(settings[hosts[1]]['lab_fresh_install_veyon'], False)
                log_path = self.queue.directory / (result['runs'][0]['id'] + '.log')
                os.utime(log_path, (1, 1))
                first = next(run for run in self.queue.fresh_state()['runs'] if run['host'] == hosts[0])
                self.assertTrue(first['quiet'])
                self.assertIn('Testaufgabe', first['task'])
                self.queue.stop(result['runs'][2]['id'], True)
            finally:
                release.set()
                self.until(lambda: not self.deployments.lock.locked())
        self.assertEqual({r['host']: r['status'] for r in self.queue.records()},
                         dict(zip(hosts, ('succeeded', 'succeeded', 'stopped'))))
        self.assertTrue((self.admissions / (hosts[2] + '.json')).exists())

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


class BootstrapTests(unittest.TestCase):
    def test_keyscan_requires_expected_fingerprint_and_address(self):
        blob = b'fake-key'
        line = '10.255.40.1 ssh-ed25519 ' + base64.b64encode(blob).decode()
        fingerprint = 'SHA256:' + base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip('=')
        with patch.object(admission.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, line)):
            self.assertEqual(admission.verified_key('10.255.40.1', fingerprint), line + '\n')
            with self.assertRaises(ValueError):
                admission.verified_key('10.255.40.2', fingerprint)
            with self.assertRaises(ValueError):
                admission.verified_key('10.255.40.1', 'SHA256:' + 'a' * 43)

    def test_passwords_are_only_passed_as_anonymous_files(self):
        passwords = ('ssh test password', 'sudo test password')
        original_run = subprocess.run

        def run(command, **kwargs):
            self.assertFalse(any(password in ' '.join(command) for password in passwords))
            paths = [command[command.index(option) + 1]
                     for option in ('--connection-password-file', '--become-password-file')]
            for fd in kwargs['pass_fds']:
                self.assertEqual(os.fstat(fd).st_mode & 0o777, 0o600)
            code = 'from ansible.cli import CLI; import sys; print(*map(CLI.get_password_from_file, sys.argv[1:]), sep="\\n")'
            process = original_run(['/usr/bin/python3', '-B', '-c', code, *paths], text=True,
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
