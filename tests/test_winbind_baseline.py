"""Guard the version boundary, idempotency and rollback of the LAB IDMAP module."""
import base64
import builtins
import hashlib
import importlib.util
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch


SOURCE = Path(__file__).resolve().parents[1] / 'tasks/winbind/install.py'
spec = importlib.util.spec_from_file_location('lab_winbind_install', SOURCE)
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class WinbindBaselineTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(self.enterContext(TemporaryDirectory()))
        self.module = self.directory / 'ad.so'
        self.original = b'\x7fELFvendor'
        self.candidate = b'\x7fELFcandidate'
        self.module.write_bytes(self.original)
        self.module.chmod(0o644)
        self.enterContext(patch.object(installer, 'MODULE', self.module))
        self.enterContext(patch.object(installer, 'BACKUPS', self.directory/'backups'))
        self.enterContext(patch.object(installer.os, 'geteuid', return_value=0))
        self.enterContext(patch.object(installer.socket, 'gethostname', return_value='LAB-08A'))
        self.version = self.enterContext(patch.object(installer, 'check_version'))
        self.enterContext(patch.object(installer, 'check_module', side_effect=lambda: self.module.stat()))
        self.enterContext(patch.object(installer, 'resolve_identity', return_value={'user':'2596','group':'15837'}))
        self.idle = self.enterContext(patch.object(installer, 'require_idle'))
        self.verify = self.enterContext(patch.object(installer, 'verify'))
        self.command = self.enterContext(patch.object(installer, 'command'))
        self.enterContext(patch.object(installer.signal, 'signal'))
        real_open = builtins.open
        for name in ('lock', 'lock-frontend'):
            (self.directory/name).touch()
        def open_file(path, *args, **kwargs):
            if str(path).startswith('/var/lib/dpkg/'):
                path = self.directory/Path(path).name
            return real_open(path, *args, **kwargs)
        self.enterContext(patch('builtins.open', side_effect=open_file))
        digest = lambda data: hashlib.sha256(data).hexdigest()
        self.data = dict(host='LAB-08A',student='s.test',candidate=base64.b64encode(self.candidate).decode(),
                         patch='retry patch\n',manifest=dict(vendor_version='exact-version',
                         vendor_module_sha256=digest(self.original),candidate_sha256=digest(self.candidate),
                         patch_sha256=digest(b'retry patch\n')))

    def run_install(self):
        with patch.object(installer.sys, 'stdin', io.StringIO(json.dumps(self.data))):
            installer.main()

    def test_success_and_retry_do_not_restart_twice(self):
        self.run_install()
        self.assertEqual(self.module.read_bytes(),self.candidate)
        backup = next((self.directory/'backups').iterdir())
        self.assertEqual((backup/'ad.so.vendor').read_bytes(),self.original)
        self.assertTrue(json.loads((backup/'manifest.json').read_text())['applied'])
        with self.assertRaises(SystemExit) as exit:
            self.run_install()
        self.assertEqual(exit.exception.code,0)
        self.command.assert_called_once()

    def test_failed_verification_restores_original_atomically(self):
        self.verify.side_effect=RuntimeError('verification failed')
        with self.assertRaises(RuntimeError):
            self.run_install()
        self.assertEqual(self.module.read_bytes(),self.original)
        self.assertEqual(self.command.call_count,2)
        backup=next((self.directory/'backups').iterdir())
        self.assertFalse(json.loads((backup/'manifest.json').read_text())['applied'])

    def test_unknown_module_or_version_never_changes_installed_bytes(self):
        self.module.write_bytes(b'unknown module')
        with self.assertRaises(SystemExit):
            self.run_install()
        self.assertEqual(self.module.read_bytes(),b'unknown module')
        self.command.assert_not_called()
        self.version.side_effect=RuntimeError('wrong Samba version')
        with self.assertRaises(RuntimeError):
            self.run_install()
        self.command.assert_not_called()
