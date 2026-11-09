"""Explicit cleanup only removes reviewed LAB archives, keeping recovery points."""
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import threading
import unittest
from unittest.mock import Mock, patch

from dashboard import backup
from dashboard.deploy import Deployments
from maintenance.preview import BERLIN


class CleanupTests(unittest.TestCase):
    def setUp(self):
        self.home=Path(self.enterContext(TemporaryDirectory()))
        self.enterContext(patch.object(backup,'HOME',self.home))
        self.lock=threading.Lock()
        self.deploys=SimpleNamespace(lock=self.lock,validate_id=Deployments.validate_id)
        self.manager=backup.Backups(self.home/'state/backup',self.deploys,self.home/'state',profile='apps')
        self.manager.transfer_ready=Mock(return_value=True)
        self.remote=self.enterContext(patch.object(self.manager,'prune_remote_apps',return_value='deleted'))
        self.old,self.path=self.archive('a',age=4)
        self.current,self.current_path=self.archive('b',age=0,confirmed=True)

    def archive(self,letter,age=1,local=True,remote=True,confirmed=False):
        started=(datetime.now(BERLIN)-timedelta(days=age)).isoformat()
        name=f'CISCO-LAB-APP-Konfiguration-20261108-120000-{letter*8}.tar.gz.gpg'
        content=b'encrypted fixture '+letter.encode();digest=hashlib.sha256(content).hexdigest()
        path=self.manager.archive_directory()/name
        if local:
            path.write_bytes(content);path.chmod(0o600)
            checksum=path.with_suffix('.gpg.sha256');checksum.write_text(f'{digest}  {name}\n');checksum.chmod(0o600)
        run=dict(id=letter*32,started=started,status='finished',file=name,size=len(content),sha256=digest,
                 data_copy=local,copied=remote,local_saved_at=started,copied_at=started)
        if confirmed:run['confirmed_good_at']=started
        self.manager.save(run);return run,path

    def execute(self,plan):
        with patch.object(backup.threading,'Thread') as worker:
            job=self.manager.cleanup(plan['token'],True)
        worker.assert_called_once()
        self.manager.execute_cleanup(*worker.call_args.kwargs['args'])
        return self.manager.cleanup_state()

    def test_preview_is_read_only_and_protects_independent_latest_copies_and_last_restore(self):
        latest_mac,_=self.archive('c',age=-1,local=False)
        latest_local,_=self.archive('d',age=-2,remote=False)
        with patch.object(backup.hashlib,'file_digest',side_effect=AssertionError('Preview must not read full archives')):
            plan=self.manager.cleanup_plan()
        self.assertEqual({row['id'] for row in plan['candidates']},{self.old['id']})
        self.assertEqual(set(plan['protected']),{self.current['id'],latest_mac['id'],latest_local['id']})
        self.assertTrue(self.path.exists());self.remote.assert_not_called()
        self.assertFalse(self.lock.locked());self.assertIsNone(self.manager.cleanup_state())
        self.assertEqual(plan['bytes'],dict(apps=self.old['size'],mac=self.old['size']))

    def test_review_token_and_confirmation_are_required_and_changes_abort_before_deletion(self):
        plan=self.manager.cleanup_plan()
        for token,confirmed in ((plan['token'],False),('wrong',True),(None,True)):
            with self.subTest(token=token),self.assertRaises(ValueError):self.manager.cleanup(token,confirmed)
        self.path.write_bytes(b'changed fixture')
        with self.assertRaisesRegex(ValueError,'geändert'):self.manager.cleanup(plan['token'],True)
        self.assertTrue(self.path.exists());self.remote.assert_not_called();self.assertFalse(self.lock.locked())
        self.assertIsNone(self.manager.cleanup_state())

    def test_success_removes_exact_archive_pairs_keeps_history_and_latest_and_updates_status(self):
        unrelated=self.path.parent/'other-project.txt';unrelated.write_text('leave alone')
        job=self.execute(self.manager.cleanup_plan())
        self.assertEqual(job['status'],'finished');self.assertEqual(job['removed'],1)
        self.assertEqual(job['bytes'],dict(apps=self.old['size'],mac=self.old['size']))
        self.assertFalse(self.path.exists());self.assertFalse(self.path.with_suffix('.gpg.sha256').exists())
        self.assertTrue(self.current_path.exists());self.assertTrue(unrelated.exists())
        self.remote.assert_called_once_with(self.old['file'],self.old['sha256'])
        states={run['id']:run for run in self.manager.history()}
        self.assertEqual(states[self.old['id']]['pruned_reason'],'manual_cleanup')
        self.assertFalse(self.manager.download_available(states[self.old['id']]))
        state=self.manager.state();self.assertEqual(len(state['runs']),2)
        self.assertEqual(state['latest']['id'],self.current['id']);self.assertFalse(state['busy'])
        self.assertEqual(state['cleanup']['id'],job['id']);self.assertFalse(self.lock.locked())
        self.assertEqual(self.manager.cleanup_plan()['candidates'],[])

    def test_remote_failure_preserves_local_pair_and_partial_local_failure_is_reported_per_copy(self):
        self.remote.side_effect=ValueError('receiver failed')
        job=self.execute(self.manager.cleanup_plan())
        self.assertEqual(job['status'],'failed');self.assertTrue(self.path.exists())
        self.assertTrue(self.path.with_suffix('.gpg.sha256').exists())
        self.assertEqual(job['bytes'],dict(apps=0,mac=0))
        self.remote.side_effect=None;self.remote.reset_mock()
        original=Path.unlink
        def deny(path,*args,**kwargs):
            if path==self.path:raise PermissionError('fixture')
            return original(path,*args,**kwargs)
        with patch.object(Path,'unlink',deny):job=self.execute(self.manager.cleanup_plan())
        self.assertEqual(job['status'],'failed');self.assertTrue(self.path.exists())
        run=next(row for row in self.manager.history() if row['id']==self.old['id'])
        self.assertIn('mac_removed_at',run);self.assertNotIn('apps_removed_at',run)
        plan=self.manager.cleanup_plan();self.assertTrue(plan['candidates'][0]['apps']);self.assertFalse(plan['candidates'][0]['mac'])
        self.remote.reset_mock();job=self.execute(plan)
        self.assertEqual(job['status'],'finished');self.remote.assert_not_called()

    def test_unsafe_pairs_symlinks_and_duplicate_metadata_are_skipped_without_remote_action(self):
        digest=self.path.with_suffix('.gpg.sha256')
        digest.write_text('wrong digest')
        plan=self.manager.cleanup_plan();self.assertEqual(plan['candidates'],[]);self.assertIn(self.old['id'],plan['skipped'])
        digest.write_text(f"{self.old['sha256']}  {self.old['file']}\n")
        self.path.unlink();self.path.symlink_to(self.manager.key)
        plan=self.manager.cleanup_plan();self.assertEqual(plan['candidates'],[])
        self.path.unlink();self.path.write_bytes(b'encrypted fixture a');self.path.chmod(0o600)
        duplicate=dict(self.old,id='d'*32);self.manager.save(duplicate)
        plan=self.manager.cleanup_plan();self.assertEqual(plan['candidates'],[])
        self.assertIn(self.old['id'],plan['skipped']);self.assertIn(duplicate['id'],plan['skipped'])
        self.remote.assert_not_called();self.assertTrue(self.manager.key.is_file())

    def test_busy_jobs_and_restart_do_not_create_delayed_deletions(self):
        plan=self.manager.cleanup_plan();self.lock.acquire()
        try:self.assertIsNone(self.manager.cleanup(plan['token'],True))
        finally:self.lock.release()
        self.manager.save_cleanup(dict(id='c'*32,status='running',total=1,processed=0,removed=0,bytes=dict(apps=0,mac=0),errors=[]))
        other=backup.Backups(self.manager.directory,self.deploys,self.manager.state_dir,profile='apps')
        self.assertEqual(other.cleanup_state()['status'],'interrupted')
        self.assertFalse(other.state()['busy']);self.assertTrue(self.path.exists());self.remote.assert_not_called()

    def test_old_owned_readable_archives_are_supported_but_foreign_writes_are_rejected(self):
        self.path.chmod(0o644);self.path.with_suffix('.gpg.sha256').chmod(0o644)
        plan=self.manager.cleanup_plan();self.assertEqual(len(plan['candidates']),1)
        self.assertEqual(self.execute(plan)['status'],'finished')
        old,path=self.archive('c',age=4);path.chmod(0o664)
        plan=self.manager.cleanup_plan();self.assertEqual(plan['candidates'],[]);self.assertIn(old['id'],plan['skipped'])
        self.assertTrue(path.exists())


if __name__=='__main__':unittest.main()
