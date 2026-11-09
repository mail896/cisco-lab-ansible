"""Group composition, fixed targets and actual queue execution with local doubles."""
from email.message import Message
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from dashboard.playbook_groups import PlaybookGroups
from dashboard.software import Software, package_revision
from dashboard.server import Handler


class GroupTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(TemporaryDirectory()))
        self.pin = self.root/'pin'; self.pin.write_text('fixture-hostkey')
        self.deployments = SimpleNamespace(lock=threading.Lock(), command=Mock(side_effect=lambda host, job, **kw: ['module', host, job]))
        self.software = Software(self.root/'software', self.deployments)
        self.enterContext(patch.object(self.software, 'enrolled', return_value={host: dict(known_hosts=str(self.pin)) for host in ('LAB-01A', 'LAB-08B')}))
        self.enterContext(patch('dashboard.software.reserved', return_value=False))
        self.enterContext(patch('dashboard.playbook_groups.reserved', return_value=False))
        self.groups = PlaybookGroups(self.root/'groups', self.deployments, self.software)
        self.profile = dict(id='a'*32, name='Netzwerktools', packages=['filezilla'], baseline=False,
                            approved=package_revision(['filezilla']))
        Software.atomic(self.software.directory/'profiles'/(self.profile['id']+'.json'), self.profile)

    def test_composition_survives_reload_and_is_frozen_for_new_installation(self):
        row = self.groups.save_group('', 'Unterricht', ['serial', 'software:'+self.profile['id'], 'firefox'])
        snapshot = self.groups.fresh_selection(row['id'])
        self.groups.save_group(row['id'], 'Geändert', ['session'])
        self.assertEqual(snapshot['name'], 'Unterricht')
        self.assertEqual([step['id'] for step in snapshot['steps']], ['serial','software:'+self.profile['id'],'firefox'])
        self.assertEqual(snapshot['steps'][1]['packages'], ['filezilla'])
        reloaded = PlaybookGroups(self.groups.directory, self.deployments, self.software)
        self.assertEqual(reloaded.groups()[0]['steps'], ['session'])
        self.assertEqual((self.groups.directory/'groups'/(row['id']+'.json')).stat().st_mode & 0o777, 0o600)

    def test_unknown_yaml_duplicate_steps_and_unreviewed_profile_are_rejected(self):
        for steps in ([], ['serial','serial'], ['../fresh-client.yml'], ['bootstrap'], ['software:../../key'], ['serial']*26):
            with self.subTest(steps=steps), self.assertRaises(ValueError):
                self.groups.save_group('', 'Ungültig', steps)
        self.profile['packages'] = ['gimp']
        Software.atomic(self.software.directory/'profiles'/(self.profile['id']+'.json'), self.profile)
        with self.assertRaisesRegex(ValueError, 'Pilotrechner'):
            self.groups.save_group('', 'Geändert', ['software:'+self.profile['id']])

    def test_reject_non_lab_unenrolled_teacher_and_duplicate_targets_and_release_lock(self):
        row = self.groups.save_group('', 'Konsole', ['serial'])
        for targets in (['203.0.113.254'], ['LAB-Teacher'], ['LAB-02A'], ['LAB-01A']*2):
            with self.subTest(targets=targets), self.assertRaises(ValueError):
                self.groups.start(row['id'], targets, True)
            self.assertFalse(self.deployments.lock.locked())
        self.deployments.lock.acquire()
        self.assertIsNone(self.groups.start(row['id'], ['LAB-01A'], True))
        self.deployments.lock.release()
        with self.assertRaises(ValueError): self.groups.start(row['id'], ['LAB-01A'], False)

    def test_executes_steps_in_order_stops_failed_host_and_preserves_other_results(self):
        row = self.groups.save_group('', 'Unterricht', ['serial','software:'+self.profile['id'],'firefox'])
        with patch('dashboard.playbook_groups.threading.Thread') as thread:
            reply = self.groups.start(row['id'], ['LAB-01A','LAB-08B'], True)
        run = thread.call_args.kwargs['args'][0]
        calls = []
        def execute(command, **kw):
            calls.append(command)
            kw['stdout'].write('Fixture '+str(command)+'\n')
            return SimpleNamespace(returncode=1 if command == ['module','LAB-08B','serial'] else 0)
        with patch.object(self.software, 'on_host_command', side_effect=lambda host, pin, selected: ['software',host,*selected]), \
                patch('dashboard.playbook_groups.subprocess.run', side_effect=execute), \
                patch('dashboard.playbook_groups.close_control_socket'):
            self.groups.execute(run)
        self.assertEqual([call[0::2] for call in calls if call[1]=='LAB-01A'],
                         [['module','serial'],['software','filezilla'],['module','firefox']])
        self.assertEqual([call for call in calls if call[1]=='LAB-08B'], [['module','LAB-08B','serial']])
        self.assertEqual(run['results']['LAB-01A']['status'], 'succeeded')
        self.assertEqual(run['results']['LAB-08B']['status'], 'failed')
        self.assertEqual(run['status'], 'failed')
        self.assertFalse(self.deployments.lock.locked())
        self.groups.delete_group(row['id'], True)
        self.assertIn('filezilla', self.groups.output(reply['id'], 'LAB-01A'))
        self.assertEqual(self.groups.records()[0]['group']['name'], 'Unterricht')
        with self.assertRaises(ValueError): self.groups.output(reply['id'], 'LAB-02A')

    def test_changed_host_key_and_maintenance_reservation_prevent_commands(self):
        row = self.groups.save_group('', 'Konsole', ['serial'])
        for changed in (True, False):
            with patch('dashboard.playbook_groups.threading.Thread') as thread:
                self.groups.start(row['id'], ['LAB-01A'], True)
            run = thread.call_args.kwargs['args'][0]
            if changed: self.pin.write_text('changed')
            with patch('dashboard.playbook_groups.reserved', return_value=not changed), patch('dashboard.playbook_groups.subprocess.run') as call:
                self.groups.execute(run)
                call.assert_not_called()
            self.assertEqual(run['status'], 'failed')
            self.assertFalse(self.deployments.lock.locked())

    def test_api_is_readable_but_all_writes_require_admin(self):
        for endpoint, payload in (
                ('save',dict(identity='',name='Kurs',steps=['serial'])),
                ('delete',dict(identity='b'*32,confirmed=True)),
                ('start',dict(group_id='b'*32,targets=['LAB-01A'],confirmed=True))):
            handler = Handler.__new__(Handler)
            handler.path = '/cisco-lab/api/playbook-groups/'+endpoint
            handler.server = SimpleNamespace(base_path='/cisco-lab',playbook_groups=Mock())
            handler._trusted_request = Mock(return_value=True)
            handler._authorized = Mock(return_value=True)
            handler._csrf = Mock(return_value=True)
            handler._admin = Mock(return_value=False)
            handler.do_POST()
            handler._admin.assert_called_once()
            self.assertFalse(handler.server.playbook_groups.mock_calls)
        handler = Handler.__new__(Handler)
        handler.path = '/cisco-lab/api/playbook-groups/save'
        handler.server = SimpleNamespace(base_path='/cisco-lab',playbook_groups=self.groups)
        for method in ('_trusted_request','_authorized','_csrf','_admin'):
            setattr(handler, method, Mock(return_value=True))
        raw = json.dumps(dict(identity='',name='Kurs',steps=['serial'])).encode()
        handler.headers = Message(); handler.headers['Content-Length'] = str(len(raw)); handler.headers['Content-Type'] = 'application/json'
        handler.rfile = io.BytesIO(raw); handler._json = Mock()
        handler.do_POST()
        self.assertEqual(handler._json.call_args.args[0],200)
        self.assertEqual(self.groups.groups()[0]['name'],'Kurs')
