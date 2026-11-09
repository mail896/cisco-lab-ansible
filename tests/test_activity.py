"""Read-only banner metadata from small synthetic state files, no LAB calls."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from dashboard.activity import Activity


class ActivityTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(TemporaryDirectory()))
        self.lock = Mock()
        self.lock.locked.return_value = True
        self.activity = Activity(self.root, self.lock)

    def record(self, folder, name, **fields):
        path = self.root / folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(dict(status='running', started='2026-11-08T17:00:00+01:00', **fields)))
        return path

    def test_idle_status_does_not_read_any_history_or_trust_stale_running_records(self):
        self.record('room', 'old.json', action='poweroff', targets=['LAB-08A'])
        self.lock.locked.return_value = False
        with patch.object(Path, 'glob', side_effect=AssertionError('Idle status read history')):
            self.assertEqual(self.activity.snapshot(), dict(busy=False))

    def test_busy_room_summary_only_exposes_allowed_names_and_static_link(self):
        self.record('room', 'current.json', action='poweroff', targets=['LAB-08A','LAB-Teacher','untrusted'],
                    error='must not be displayed', ssh_password='must not be displayed')
        self.assertEqual(self.activity.snapshot(), dict(busy=True, label='Herunterfahren',
            hosts=['LAB-08A','LAB-Teacher'],started='2026-11-08T17:00:00+01:00',href='/sammelaktionen'))
        self.lock.locked.return_value = False
        self.assertEqual(self.activity.snapshot(), dict(busy=False))

    def test_fresh_queue_includes_every_active_target(self):
        self.record('provision', 'one.json', host='LAB-08B')
        self.record('provision', 'two.json', host='LAB-01A')
        summary = self.activity.snapshot()
        self.assertEqual(summary['hosts'], ['LAB-01A','LAB-08B'])
        self.assertEqual(summary['href'], '/installationsfolge')

    def test_bulk_read_and_access_banner_includes_each_active_client(self):
        for folder,kind,label in (('provision/intake',{},'Geräteprüfung'),
                                  ('provision',dict(kind='bootstrap'),'SSH-Erstaufnahme')):
            with self.subTest(label=label):
                first=self.record(folder,'read-one.json',host='LAB-01A',**kind)
                second=self.record(folder,'read-two.json',host='LAB-01B',**kind)
                self.activity.cached=None
                summary=self.activity.snapshot()
                self.assertEqual(summary['label'],label)
                self.assertEqual(summary['hosts'],['LAB-01A','LAB-01B'])
                first.unlink();second.unlink()

    def test_cleanup_teacher_and_rollout_use_their_own_workflow_links(self):
        for folder, name, fields, label, href in (
            ('backup','cleanup-state',{},'Alte Sicherungen bereinigen','/backup'),
            ('teacher','run-fixture.json',{},'Teacher-Wartung','/wartung'),
            ('appearance-releases','fixture.json',dict(job='firefox'),'Raumverteilung','/playbooks'),
            ('software/runs','fixture.json',dict(kind='plan',targets=['LAB-08A']),'Softwareinstallation prüfen','/repo-software'),
            ('playbook-groups/runs','fixture.json',dict(group=dict(name='Netzwerkkurs'),targets=['LAB-08A']),'Playbook-Gruppe: Netzwerkkurs','/playbooks'),
        ):
            with self.subTest(folder=folder):
                path = self.record(folder, name, **fields)
                self.activity.cached = None
                summary = self.activity.snapshot()
                self.assertEqual((summary['label'],summary['href']), (label,href))
                path.unlink()

    def test_missing_or_damaged_metadata_reports_busy_without_inventing_a_job(self):
        path = self.record('room','bad.json')
        path.write_text('{broken')
        summary = self.activity.snapshot()
        self.assertTrue(summary['busy'])
        self.assertIsNone(summary['href'])
        self.assertIsNone(summary['started'])
        self.assertEqual(summary['hosts'], [])

    def test_large_bounded_software_plan_keeps_its_useful_banner(self):
        dependencies=['dependency-'+str(index) for index in range(7000)]
        path=self.record('software/runs','large.json',kind='plan',targets=['LAB-08A'],packages=dependencies)
        self.assertGreater(path.stat().st_size,65536)
        summary=self.activity.snapshot()
        self.assertEqual(summary['label'],'Softwareinstallation prüfen')
        self.assertEqual(summary['hosts'],['LAB-08A'])
        self.assertEqual(summary['href'],'/repo-software')
