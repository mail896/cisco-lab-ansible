"""Independent backup destinations, private streaming ZIPs and recovery metadata."""
from datetime import datetime, timedelta
import base64
import hashlib
import http.client
from http.server import ThreadingHTTPServer
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import threading
import unittest
from unittest.mock import Mock, patch
import zipfile

from dashboard import backup
from dashboard.deploy import Deployments
from dashboard.server import Handler
from maintenance.preview import BERLIN


class BackupTargetTests(unittest.TestCase):
    def setUp(self):
        self.root=Path(self.enterContext(TemporaryDirectory()))
        self.home=self.root/'home';self.home.mkdir(mode=0o700)
        self.code=self.root/'code';(self.code/'deployment/apps').mkdir(parents=True)
        (self.code/'deployment/apps/RESTORE.txt').write_text('LAB restore guide fixture')
        self.enterContext(patch.object(backup,'HOME',self.home))
        self.enterContext(patch.object(backup,'ROOT',self.code))
        self.enterContext(patch.object(backup,'apps_system_sources',return_value=()))
        self.lock=threading.Lock()
        self.manager=backup.Backups(self.home/'state/backup',SimpleNamespace(lock=self.lock,validate_id=Deployments.validate_id),self.home/'state',profile='apps')
        self.manager.transfer_ready=Mock(return_value=True)
        self.uploads=[];self.manifests=[]
        self.manager.apps_upload=Mock(side_effect=lambda path,digest:self.uploads.append((path.read_bytes(),digest)))

    def run_fixture(self, targets, error=False):
        class ImmediateThread:
            def __init__(inner,target,args,**kwargs):inner.target,inner.args=target,args
            def start(inner):inner.target(*inner.args)
        def source(args,**kwargs):
            manifest=next(self.manager.directory.glob('restore-info-*/restore-manifest.json'))
            self.manifests.append(json.loads(manifest.read_text()))
            process=Mock();process.stdout=io.BytesIO(b'fixture tar');process.wait.return_value=0
            context=Mock();context.__enter__=Mock(return_value=process);context.__exit__=Mock(return_value=False)
            return context
        def encrypt(args,**kwargs):
            path=Path(args[args.index('--output')+1]);path.write_bytes(b'encrypted fixture');path.chmod(0o600)
            return SimpleNamespace(returncode=0)
        if error:self.manager.apps_upload.side_effect=ValueError('fixture upload failure')
        with patch.object(backup,'threading',SimpleNamespace(Thread=ImmediateThread)),patch.object(backup.subprocess,'Popen',side_effect=source),patch.object(backup.subprocess,'run',side_effect=encrypt):
            return self.manager.start(targets=targets)

    def archive(self, letter='a', **fields):
        name=f'CISCO-LAB-APP-Konfiguration-20261108-120000-{letter*8}.tar.gz.gpg'
        path=self.manager.archive_directory()/name;path.write_bytes(b'encrypted fixture');path.chmod(0o600)
        run=dict(id=letter*32,status='finished',started=datetime.now(BERLIN).isoformat(),file=name,
                 data_copy=True,size=path.stat().st_size,sha256=hashlib.sha256(path.read_bytes()).hexdigest())
        run.update(fields);self.manager.save(run)
        return run,path

    def test_all_destination_combinations_make_one_archive_and_only_requested_persistent_copies(self):
        for targets in (['apps'],['mac'],['download'],['apps','mac'],['apps','download'],['mac','download'],['apps','mac','download']):
            with self.subTest(targets=targets):
                previous=len(self.manifests);uploads=len(self.uploads)
                run=self.run_fixture(targets)
                self.assertEqual(run['status'],'finished',run.get('error'))
                self.assertEqual(len(self.manifests),previous+1)
                self.assertEqual(run['targets'],targets)
                self.assertEqual(run['data_copy'],'apps' in targets)
                self.assertEqual(bool(run.get('copied')),'mac' in targets)
                self.assertEqual(len(self.uploads)-uploads,int('mac' in targets))
                self.assertEqual((self.manager.local_directory()/run['file']).exists(),'apps' in targets)
                self.assertEqual(self.manager.download_available(run),'apps' in targets or 'download' in targets)
                self.assertEqual(bool(run.get('download_cache')),'download' in targets and 'apps' not in targets)
                self.assertFalse(list(self.manager.directory.glob('transfer-*')))
                self.assertFalse(list(self.manager.directory.glob('restore-info-*')))
                self.assertFalse(self.lock.locked())
                self.assertIn('recovery-key',self.manifests[-1]['separately_required'])

    def test_invalid_targets_rejected_before_lock_or_work(self):
        for targets in ([],None,'apps',[True],[{}],['apps','apps'],['other'],['apps','mac','download','apps']):
            with self.subTest(targets=targets),self.assertRaises(ValueError):self.manager.start(targets=targets)
        self.assertFalse(self.lock.locked());self.assertFalse(self.manager.history())
        self.manager.transfer_ready.return_value=False
        with self.assertRaises(ValueError):self.manager.start(targets=['mac'])
        self.assertEqual(self.run_fixture(['download'])['status'],'finished')

    def test_failed_mac_does_not_hide_local_or_download_success(self):
        run=self.run_fixture(['apps','mac','download'],error=True)
        self.assertEqual(run['status'],'failed');self.assertTrue(run['data_copy'])
        self.assertTrue(self.manager.download_available(run));self.assertNotIn('copied',run)
        state=self.manager.state();self.assertEqual(state['locations']['apps']['last']['id'],run['id'])
        self.assertIsNone(state['locations']['mac']['last'])
        self.assertIn('Lokales Archiv bleibt erhalten',run['error'])

    def test_independent_last_times_use_success_for_each_destination_even_in_failed_run(self):
        old='2026-11-07T12:00:00+01:00';new='2026-11-08T12:00:00+01:00'
        self.archive('a',started=old,copied=True,copied_at=old)
        latest,_=self.archive('b',started=new,status='failed',local_saved_at=new)
        state=self.manager.state(limit=1)
        self.assertEqual(state['locations']['apps']['last']['time'],new)
        self.assertEqual(state['locations']['mac']['last']['time'],old)
        self.assertEqual(state['latest']['id'],latest['id']);self.assertEqual(len(state['runs']),1)
        self.assertEqual(state['locations']['mac']['path'],'/Volumes/Repo/cisco-lab')
        self.manager.apps_upload.assert_not_called()

    def test_zip_stream_contains_encrypted_member_checksum_and_guide_without_recovery_key(self):
        run,path=self.archive()
        class Sink:
            def __init__(inner):inner.data=bytearray()
            def write(inner,data):inner.data.extend(data);return len(data)
            def flush(inner):pass
        output=Sink()
        with self.manager.download(run['id']) as (record,source):self.manager.write_zip(record,source,output)
        with zipfile.ZipFile(io.BytesIO(output.data)) as saved:
            self.assertIsNone(saved.testzip())
            self.assertEqual(set(saved.namelist()),{run['file'],run['file']+'.sha256','RESTORE.txt'})
            self.assertEqual(saved.read(run['file']),path.read_bytes())
            self.assertEqual(saved.read(run['file']+'.sha256').decode(),f"{run['sha256']}  {run['file']}\n")
            self.assertEqual(saved.read('RESTORE.txt'),b'LAB restore guide fixture')
            self.assertNotIn(self.manager.key.read_bytes(),output.data)

    def test_download_rejects_traversal_symlinks_nonprivate_and_expired_cache(self):
        run,path=self.archive()
        for value in ('../recovery-key','x','a'*31+'z'):
            with self.assertRaises((ValueError,FileNotFoundError)),self.manager.download(value):pass
        path.unlink();path.symlink_to(self.manager.key)
        with self.assertRaises(OSError),self.manager.download(run['id']):pass
        path.unlink();path.write_bytes(b'encrypted fixture');path.chmod(0o644)
        self.assertFalse(self.manager.download_available(run))
        with self.assertRaises(ValueError),self.manager.download(run['id']):pass
        path.chmod(0o600)
        run.update(data_copy=False,download_cache=True,download_expires_at=(datetime.now(BERLIN)-timedelta(seconds=1)).isoformat())
        cache=self.manager.download_directory()/run['file'];cache.write_bytes(path.read_bytes());cache.chmod(0o600)
        self.manager.save(run)
        self.assertFalse(self.manager.download_available(run))
        with self.assertRaises(ValueError),self.manager.download(run['id']):pass
        self.manager.clean_downloads();self.assertFalse(cache.exists());self.assertTrue(path.exists())

    def test_real_http_zip_requires_login_has_private_headers_and_finishes_stream(self):
        run,_=self.archive()
        password=self.root/'passwort';password.write_text('fixture-only');password.chmod(0o600)
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        server.auth=None;server.password_path=password;server.base_path='/cisco-lab';server.https_host=None;server.backups=self.manager
        worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        credential='Basic '+base64.b64encode(b'lab-admin:fixture-only').decode()
        for method,authenticated in (('GET',False),('GET',True),('HEAD',True)):
            connection=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=5)
            try:
                connection.request(method,'/cisco-lab/api/backup/download/'+run['id'],headers={'Authorization':credential} if authenticated else {})
                response=connection.getresponse();body=response.read()
                self.assertEqual(response.status,200 if authenticated else 401)
                self.assertEqual(response.getheader('Cache-Control'),'no-store')
                if authenticated:
                    self.assertEqual(response.getheader('Content-Type'),'application/zip')
                    self.assertIn('.zip',response.getheader('Content-Disposition'))
                    if method=='GET':
                        with zipfile.ZipFile(io.BytesIO(body)) as saved:self.assertIsNone(saved.testzip())
                    else:self.assertEqual(body,b'')
            finally:connection.close()

    def test_retention_preserves_latest_mac_copy_when_latest_local_is_different(self):
        old=(datetime.now(BERLIN)-timedelta(days=120)).isoformat()
        local,_=self.archive('a',started=old,local_saved_at=old,targets=['apps'])
        remote,_=self.archive('b',started=old,copied=True,targets=['mac'],data_copy=False)
        stale,path=self.archive('c',started=old,copied=True,targets=['mac'],data_copy=False)
        # These are remote-only records: no local archive pairs are required.
        for run in (remote,stale):(self.manager.local_directory()/run['file']).unlink()
        remote['copied_at']=(datetime.now(BERLIN)-timedelta(days=100)).isoformat();self.manager.save(remote)
        with patch.object(self.manager,'prune_remote_apps') as delete:self.manager.prune(io.StringIO())
        delete.assert_called_once_with(stale['file'],stale['sha256'])
        states={run['id']:run for run in self.manager.history()}
        self.assertNotIn('pruned_at',states[local['id']]);self.assertNotIn('pruned_at',states[remote['id']])
        self.assertIn('pruned_at',states[stale['id']])


if __name__=='__main__':unittest.main()
