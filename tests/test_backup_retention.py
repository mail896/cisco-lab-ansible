"""Preserve the last confirmed recovery point and never prune unrelated files."""
from datetime import datetime, timedelta
import hashlib
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import threading
import unittest
from unittest.mock import patch

from dashboard.backup import Backups
from dashboard.deploy import Deployments
from maintenance.preview import BERLIN


class RetentionTests(unittest.TestCase):
    def setUp(self):
        self.root=Path(self.enterContext(TemporaryDirectory()))
        self.destination=self.root/'archives';self.destination.mkdir()
        self.directory=self.root/'state';self.directory.mkdir()
        self.enterContext(patch.object(Backups, 'archive_directory',return_value=self.destination))
        self.backups=Backups(self.directory,SimpleNamespace(lock=threading.Lock(),validate_id=Deployments.validate_id),self.root)
        self.remote=self.enterContext(patch.object(Backups, 'prune_remote'))

    def archive(self, letter, age, confirmed=False):
        name=f'LAB-Teacher-Konfiguration-20260101-120000-{letter*8}.tar.gz.gpg'
        path=self.destination/name;path.write_bytes(b'encrypted test fixture')
        path.with_suffix('.gpg.sha256').write_text('fixture checksum')
        start=datetime.now(BERLIN)-timedelta(days=age)
        run=dict(id=letter*32,started=start.isoformat(),status='finished',file=name,data_copy=True,copied=True,
                 sha256=hashlib.sha256(path.read_bytes()).hexdigest())
        if confirmed:run['confirmed_good_at']=start.isoformat()
        self.backups.save(run)
        return run,path

    def test_last_confirmed_is_kept_while_other_old_backups_are_removed(self):
        protected,path=self.archive('a',120,True)
        stale,old=self.archive('b',100)
        current,recent=self.archive('c',3)
        unrelated=self.destination/'other-project.tar.gz';unrelated.touch()
        self.backups.prune(io.StringIO())
        self.assertTrue(path.exists());self.assertTrue(recent.exists());self.assertTrue(unrelated.exists())
        self.assertFalse(old.exists())
        self.remote.assert_called_once()
        stored=json.loads((self.directory/(stale['id']+'.json')).read_text())
        self.assertIn('pruned_at',stored)

    def test_newest_is_preserved_when_nobody_has_confirmed_recovery(self):
        run,old=self.archive('a',200)
        newest,recent=self.archive('b',100)
        self.backups.prune(io.StringIO())
        self.assertFalse(old.exists());self.assertTrue(recent.exists())

    def test_failed_remote_prune_preserves_local_pair(self):
        import subprocess
        self.archive('a',120,True)
        old,path=self.archive('b',100)
        self.remote.side_effect=subprocess.CalledProcessError(1,['ssh'])
        self.backups.prune(io.StringIO())
        self.assertTrue(path.exists());self.assertTrue(path.with_suffix('.gpg.sha256').exists())

    def test_confirmation_rejects_tampered_archive_and_accepts_matching_one(self):
        run,path=self.archive('a',1)
        confirmed=self.backups.confirm(run['id'])
        self.assertIn('confirmed_good_at',confirmed)
        path.write_bytes(b'changed')
        with self.assertRaises(ValueError):self.backups.confirm(run['id'])
