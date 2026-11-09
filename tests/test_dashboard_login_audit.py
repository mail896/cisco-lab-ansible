"""Private login history persists without secrets and expires by UTC date."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from dashboard.login_audit import LoginAudit


class LoginAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)/'audit'
        self.now=[datetime(2026,11,8,12,tzinfo=timezone.utc).timestamp()]
        self.audit=LoginAudit(self.root,clock=lambda:self.now[0])

    def test_names_outcomes_persist_but_no_token_or_uuid(self):
        self.audit.append('success','signed_in',dict(account='teacher.test',name='Testperson',role='reader',
                          access='do-not-log-token',uuid='do-not-log-uuid'))
        self.now[0]+=1
        self.audit.append('denied','not_collegium',dict(account='student.test',name='Testschueler',role=None))
        again=LoginAudit(self.root,clock=lambda:self.now[0]);rows=again.records()
        self.assertEqual([row['account'] for row in rows],['student.test','teacher.test'])
        self.assertEqual(rows[0]['name'],'Testschueler')
        contents=next(self.root.iterdir()).read_text()
        self.assertNotIn('do-not-log',contents)
        self.assertEqual(next(self.root.iterdir()).stat().st_mode&0o777,0o600)

    def test_provider_denial_has_no_invented_name(self):
        self.audit.append('denied','provider_denied')
        row=self.audit.records()[0]
        self.assertIsNone(row['name']);self.assertIsNone(row['account'])

    def test_expired_records_are_removed_at_next_append(self):
        self.audit.append('success','signed_in')
        old=next(self.root.iterdir())
        self.now[0]+=91*86400
        self.assertFalse(self.audit.records())
        self.audit.append('denied','provider_denied')
        self.assertFalse(old.exists());self.assertEqual(len(self.audit.records()),1)

    def test_daily_symlink_and_unprivate_file_are_rejected(self):
        target=Path(self.temp.name)/'other';target.write_text('untouched')
        path=self.root/'2026-11-08.jsonl';path.symlink_to(target)
        with self.assertRaises(OSError):self.audit.append('denied','provider_denied')
        self.assertEqual(target.read_text(),'untouched');path.unlink()
        path.write_text('');path.chmod(0o644)
        with self.assertRaises(ValueError):self.audit.append('denied','provider_denied')


if __name__=='__main__':unittest.main()
