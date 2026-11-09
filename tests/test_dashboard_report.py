"""Report reads reuse one shared refresh after completed maintenance."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import subprocess

from dashboard.server import Dashboard


class ReportTests(unittest.TestCase):
    def test_package_lists_and_real_apt_policy_are_exposed_without_client_access(self):
        with TemporaryDirectory() as temporary:
            path = Path(temporary)/'report'
            path.write_text(json.dumps(dict(timestamp='2026-11-09T13:00:00+01:00',clients={
                'LAB-08A':dict(action='evening_logout_install',upgrade=dict(install=['curl','vim']),
                    autoremove=dict(remove=['old-package']),automatic_apt=dict(periodic_unattended='0',upgrade_timer='enabled')),
                'LAB-08B':dict(action='evening_logout_install',automatic_apt=dict(periodic_unattended='1',upgrade_timer='enabled'))})))
            with patch('dashboard.client_report.subprocess.run',side_effect=AssertionError('Unexpected client probe')):
                state = Dashboard(path,None).state()
            a,b = state['clients'][-2:]
            self.assertEqual(a['update_packages'],['curl','vim'])
            self.assertEqual(a['removal_packages'],['old-package'])
            self.assertEqual((a['updates'],a['removals'],a['apt_mode']),(2,1,'central'))
            self.assertEqual(b['apt_mode'],'local')
            self.assertEqual(state['clients'][0]['apt_mode'],'unknown')

    def test_unreachable_client_is_not_reported_as_having_zero_pending_updates(self):
        with TemporaryDirectory() as temporary:
            report=Path(temporary)/'report'
            report.write_text(json.dumps(dict(clients={
                'LAB-08A':dict(action='review',reason='Client nicht erreichbar.'),
                'LAB-08B':dict(action='evening_logout_install',upgrade=dict(install=[]),autoremove=dict(remove=[]))})))
            a,b=Dashboard(report,None).state()['clients'][-2:]
            self.assertFalse(a['updates_known']);self.assertFalse(a['removals_known'])
            self.assertEqual(a['reason'],'Client nicht erreichbar.')
            self.assertTrue(b['updates_known']);self.assertTrue(b['removals_known'])
            self.assertEqual(b['updates'],0)

    def test_completed_maintenance_refreshes_once_for_all_readers(self):
        with TemporaryDirectory() as temporary:
            report = Path(temporary)/'report'
            revision = Path(temporary)/'revision'
            report.write_text(json.dumps(dict(clients={'LAB-08A':dict(upgrade=dict(install=['apt']))})))
            revision.write_text('a'*32)
            dashboard = Dashboard(report, None, revision)
            # Hold the actual background work while several requests arrive.
            with patch('dashboard.client_report.threading.Thread') as thread:
                for _ in range(4):
                    self.assertTrue(dashboard.state()['refreshing'])
                thread.assert_called_once()
            preview = dict(timestamp='2026-11-09T18:30:00+01:00', clients={
                'LAB-08A':dict(action='evening_logout_install',upgrade=dict(install=[]))})
            with patch('dashboard.client_report.subprocess.run', return_value=subprocess.CompletedProcess([],0,json.dumps(preview))), \
                 patch('dashboard.client_report.threading.Thread') as thread:
                dashboard._refresh('a'*32)
                for _ in range(3):
                    state = dashboard.state()
                    self.assertFalse(state['refreshing'])
                    self.assertEqual(state['clients'][-2]['updates'],0)
                thread.assert_not_called()
                self.assertEqual(json.loads(report.read_text())['maintenance_revision'],'a'*32)

    def test_failed_automatic_refresh_keeps_report_and_does_not_retry_every_read(self):
        with TemporaryDirectory() as temporary:
            report = Path(temporary)/'report'
            revision = Path(temporary)/'revision'
            original=json.dumps(dict(timestamp='before', clients={}))
            report.write_text(original); revision.write_text('a'*32)
            dashboard = Dashboard(report, None, revision)
            with patch('dashboard.client_report.threading.Thread'):
                dashboard.state()
            with patch('dashboard.client_report.subprocess.run', side_effect=OSError('unavailable')):
                dashboard._refresh('a'*32)
            with patch('dashboard.client_report.threading.Thread') as thread:
                self.assertIsNotNone(dashboard.state()['refresh_error'])
                self.assertEqual(report.read_text(),original)
                thread.assert_not_called()
                revision.write_text('b'*32)
                self.assertTrue(dashboard.state()['refreshing'])
                thread.assert_called_once()

    def test_new_completion_during_preview_is_not_acknowledged_by_old_probe(self):
        with TemporaryDirectory() as temporary:
            report=Path(temporary)/'report'; revision=Path(temporary)/'revision'
            revision.write_text('a'*32)
            dashboard=Dashboard(report,None,revision)
            with patch('dashboard.client_report.threading.Thread'):
                dashboard.state()
            revision.write_text('b'*32)
            with patch('dashboard.client_report.subprocess.run', return_value=subprocess.CompletedProcess([],0,'{"clients":{}}')):
                dashboard._refresh('a'*32)
            with patch('dashboard.client_report.threading.Thread') as thread:
                self.assertTrue(dashboard.state()['refreshing'])
                thread.assert_called_once()
                self.assertEqual(thread.call_args.kwargs['args'],('b'*32,))
