"""The APP backup must stay within LAB files and keep its key separate."""
from datetime import datetime
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import threading
import unittest
from unittest.mock import Mock, patch

from dashboard import backup
from dashboard.deploy import Deployments
from maintenance.preview import BERLIN


class AppsBackupTests(unittest.TestCase):
    def test_local_encrypted_archive_excludes_unrelated_data_and_recovery_key(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            home = root / 'home'; home.mkdir(mode=0o700)
            code = root / 'app'; code.mkdir()
            (code / 'source.txt').write_text('LAB source')
            private = home / 'private'; private.mkdir(mode=0o700)
            (private / 'lab-input.txt').write_text('LAB private input')
            system = root / 'cisco-lab.service'; system.write_text('LAB unit fixture')
            (code / 'local-private').symlink_to(private)
            other = root / 'other-project.txt'; other.write_text('unrelated')
            state = home / 'state'; state.mkdir(mode=0o700)
            groups = state/'playbook-groups'/'groups'; groups.mkdir(parents=True)
            (groups/('a'*32+'.json')).write_text(json.dumps(dict(name='Netzwerkkurs', steps=['serial'])))
            lock = threading.Lock()
            deploys = SimpleNamespace(lock=lock, validate_id=Deployments.validate_id)
            with patch.object(backup, 'HOME', home), patch.object(backup, 'ROOT', code), \
                    patch.object(backup, 'apps_system_sources', return_value=(system,)), \
                    patch.object(backup, 'APPS_SSH_KEY', home / '.ssh/id_ed25519_backup'), \
                    patch.object(backup, 'APPS_KNOWN_HOSTS', home / '.ssh/mac.known_hosts'):
                manager = backup.Backups(state / 'backup', deploys, state, profile='apps')
                with self.assertRaisesRegex(ValueError, 'Externe APP-Sicherung'):
                    manager.start(True)
                run = dict(id='a' * 32, started=datetime.now(BERLIN).isoformat(),
                           status='running', transfer=False)
                lock.acquire()
                manager.execute(run)
                log = (manager.directory / (run['id'] + '.log')).read_text()
                self.assertEqual(run['status'], 'finished', f"{run.get('error')}: {log[-1000:]}")
                archive = manager.archive_directory() / run['file']
                self.assertEqual(hashlib.sha256(archive.read_bytes()).hexdigest(), run['sha256'])
                content = subprocess.check_output(
                    ['gpg', '--homedir', str(manager.directory / '.gnupg'), '--batch', '--pinentry-mode', 'loopback', '--passphrase-file',
                     str(manager.key), '--decrypt', str(archive)], stderr=subprocess.DEVNULL)
                with tarfile.open(fileobj=io.BytesIO(content), mode='r:gz') as saved:
                    names = saved.getnames()
                    manifest = json.load(saved.extractfile(run['restore_manifest_member']))
                    self.assertEqual(manifest['format'],1)
                    self.assertEqual(manifest['code'],'/opt/cisco-lab')
                    self.assertIn('recovery-key',manifest['separately_required'])
                self.assertTrue(any(name.endswith('/source.txt') for name in names))
                self.assertTrue(any(name.endswith('/lab-input.txt') for name in names))
                self.assertTrue(any(name.endswith('/cisco-lab.service') for name in names))
                self.assertTrue(any('/playbook-groups/groups/' in name for name in names))
                self.assertTrue(run['lab_system_copy'])
                self.assertFalse(any(name.endswith('/other-project.txt') for name in names))
                self.assertFalse(any(name.endswith('/recovery-key') for name in names))
                self.assertFalse(any(name.endswith('/local-private') for name in names))
                self.assertFalse(any(name.endswith('/local-private/lab-input.txt') for name in names))
                with self.assertRaisesRegex(ValueError, 'Mac-Kopie'):
                    manager.confirm(run['id'])

    def test_external_copy_uses_restricted_put_with_digest(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(backup, 'HOME', root):
                manager = backup.Backups(root / 'backup',
                                         SimpleNamespace(lock=threading.Lock()), root, profile='apps')
                name = 'CISCO-LAB-APP-Konfiguration-20261107-211718-f6a917cd.tar.gz.gpg'
                checksum = 'a' * 64
                archive = root / name
                archive.write_bytes(b'encrypted LAB fixture')
                manager.apps_request = Mock(return_value=SimpleNamespace(stdout=f'SHA256:{checksum}\n'))
                manager.apps_upload(archive, checksum)
                self.assertEqual(manager.apps_request.call_args.args[0], f'put {name} {checksum}')
                with self.assertRaisesRegex(ValueError, 'Ungültige APP-Archivmetadaten'):
                    manager.apps_upload(root / '../other-project.tar.gz.gpg', checksum)
                manager.apps_request.assert_called_once()

    def test_confirmation_requires_restore_from_mac_copy(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(backup, 'HOME', root):
                deploys = SimpleNamespace(lock=threading.Lock(), validate_id=Deployments.validate_id)
                manager = backup.Backups(root / 'backup', deploys, root, profile='apps')
                name = 'CISCO-LAB-APP-Konfiguration-20261107-211718-f6a917cd.tar.gz.gpg'
                archive = manager.archive_directory() / name
                archive.write_bytes(b'encrypted LAB fixture')
                run = dict(id='a' * 32, started=datetime.now(BERLIN).isoformat(),
                           status='finished', file=name, data_copy=True, copied=True,
                           restore_verified_at=datetime.now(BERLIN).isoformat(),
                           sha256=hashlib.sha256(archive.read_bytes()).hexdigest())
                manager.save(run)
                with self.assertRaisesRegex(ValueError, 'Mac-Kopie'):
                    manager.confirm(run['id'])
                run['remote_restore_verified_at'] = datetime.now(BERLIN).isoformat()
                manager.save(run)
                self.assertTrue(manager.confirm(run['id'])['confirmed_good_at'])


if __name__ == '__main__':
    unittest.main()
