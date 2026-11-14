"""Offline package evidence; no APT calls or LAB connections."""
from datetime import datetime
import gzip
import json
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from maintenance import controller, package_history as history
from maintenance.preview import BERLIN

START=datetime(2026,11,13,18,0,tzinfo=BERLIN)
END=datetime(2026,11,13,18,5,tzinfo=BERLIN)
COMMAND='apt-get -o DPkg::Lock::Timeout=30 -y --no-remove --with-new-pkgs upgrade'


def transaction(start='2026-11-13  18:00:20', end='2026-11-13  18:01:00', command=COMMAND, packages='Upgrade: curl:amd64 (1.0, 1.1)'):
    return 'Start-Date: '+start+'\nCommandline: '+command+'\n'+packages+'\n'+('End-Date: '+end+'\n' if end else '')+'\n'


BASE=transaction('2026-11-12  18:00:00','2026-11-12  18:01:00')


class PackageHistoryTests(unittest.TestCase):
    def test_completed_lab_changes_are_separate_from_pending_or_unrelated_changes(self):
        text=BASE+transaction(packages='Upgrade: curl:amd64 (1.0, 1.1), vim:amd64 (9.0, 9.1)\nInstall: helper:amd64 (2.0, automatic)')
        text+=transaction('2026-11-14  10:00:00','2026-11-14  10:01:00',packages='Upgrade: ignored:amd64 (1, 2)')
        result=history.summarize([text],START,END,BERLIN)
        self.assertEqual(result['status'],'recorded')
        self.assertEqual([row['name'] for row in result['upgraded']],['curl:amd64','vim:amd64'])
        self.assertEqual(result['installed'],[dict(name='helper:amd64',versions='2.0, automatic')])

    def test_cleanup_is_not_counted_as_an_update(self):
        cleanup=transaction(command='apt-get -o DPkg::Lock::Timeout=30 -y --no-auto-remove remove old-package',packages='Remove: old-package:amd64 (1.0)')
        result=history.summarize([BASE+cleanup],START,END,BERLIN)
        self.assertEqual(result['upgraded'],[])
        self.assertEqual(result['removed'],[dict(name='old-package:amd64',versions='1.0')])

    def test_zero_requires_covered_window_and_completed_maintenance_caller(self):
        result=history.summarize([BASE],START,END,BERLIN)
        self.assertEqual(result['status'],'recorded')
        self.assertEqual(result['upgraded'],[])
        for texts in ([],[''],[transaction()]):
            self.assertEqual(history.summarize(texts,START,END,BERLIN)['status'],'unavailable')

    def test_ambiguous_transactions_and_downloads_never_claim_installed_updates(self):
        for text in (
            transaction(command='apt-get upgrade'),
            transaction(command=COMMAND+' --download-only'),
            transaction(command=COMMAND+' --simulate'),
            transaction(end=''),
            transaction(end='2026-11-13  18:06:00'),
            transaction(start='2026-11-13  17:59:59'),
            transaction(packages='Upgrade: curl:amd64 (1.0, 1.1), malformed'),
        ):
            with self.subTest(text=text):
                self.assertEqual(history.summarize([BASE+text],START,END,BERLIN)['status'],'unavailable')

    def test_rotated_history_is_read_bounded_and_without_following_links(self):
        with TemporaryDirectory() as temporary:
            directory=Path(temporary)
            (directory/'history.log').write_text(transaction())
            with gzip.open(directory/'history.log.1.gz','wb') as output:output.write(BASE.encode())
            self.assertEqual(history.read_history(START,END,directory)['status'],'recorded')
            with patch.object(history,'MAX_BYTES',1):
                self.assertEqual(history.read_history(START,END,directory)['status'],'unavailable')
            (directory/'history.log').unlink()
            (directory/'history.log').symlink_to(directory/'history.log.1.gz')
            self.assertEqual(history.read_history(START,END,directory)['status'],'unavailable')

    def test_truncated_history_cannot_turn_missing_changes_into_zero_updates(self):
        for value in (BASE+'Upgrade: curl:amd64 (1, 2)\n', BASE+transaction().replace('End-Date:', 'Start-Date:')):
            with self.subTest(value=value),self.assertRaises(ValueError):
                history.summarize([value],START,END,BERLIN)

    def test_failed_remote_read_is_unknown_without_overriding_reboot_success(self):
        with patch.object(controller.subprocess,'run',side_effect=subprocess.TimeoutExpired('ssh',20)):
            result=controller.package_report('LAB-08A',{'known_hosts':'/private/pin'},START,{'time':END.isoformat()})
        self.assertEqual(result['status'],'unavailable')

    def test_remote_query_is_pinned_readonly_and_uses_exact_result_window(self):
        payload=dict(status='recorded',upgraded=[],installed=[],removed=[])
        with patch.object(controller.subprocess,'run',return_value=subprocess.CompletedProcess([],0,json.dumps(payload))) as run:
            result=controller.package_report('LAB-08A',{'known_hosts':'/private/pin'},START,{'time':END.isoformat()})
        command=run.call_args.args[0]
        self.assertIn('StrictHostKeyChecking=yes',command)
        self.assertIn('read_history',run.call_args.kwargs['input'])
        self.assertNotIn("subprocess.run(['apt",run.call_args.kwargs['input'])
        self.assertIn(END.isoformat(),command[-1])
        self.assertEqual(result,payload)
