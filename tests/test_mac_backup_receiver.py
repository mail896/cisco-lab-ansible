"""The forced Mac command accepts only named, hashed LAB archive operations."""
import hashlib
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest


SOURCE = Path(__file__).resolve().parents[1] / 'deployment/apps/mac-backup-receiver.sh'


class MacReceiverTests(unittest.TestCase):
    @unittest.skipUnless(Path('/usr/bin/shasum').is_file(), 'shasum is needed for the Mac protocol test')
    def test_upload_and_retention_cannot_touch_unrelated_files(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            destination = root / 'cisco-lab'
            destination.mkdir()
            script = root / 'receiver.sh'
            script.write_text(SOURCE.read_text().replace(
                'directory=/Volumes/Repo/cisco-lab', f'directory={destination}'))
            script.chmod(0o700)
            unrelated = destination / 'other-project.txt'
            unrelated.write_text('keep')
            name = 'CISCO-LAB-APP-Konfiguration-20261107-211718-f6a917cd.tar.gz.gpg'
            content = b'encrypted LAB fixture'
            digest = hashlib.sha256(content).hexdigest()

            def run(command, data=b''):
                return subprocess.run(['/bin/sh', str(script)], input=data,
                                      env={**os.environ, 'SSH_ORIGINAL_COMMAND': command},
                                      capture_output=True, timeout=8)

            self.assertEqual(run('check').stdout, b'ready\n')
            self.assertEqual(run(f'put {name} {digest}', content).stdout,
                             f'SHA256:{digest}\n'.encode())
            self.assertEqual((destination / name).read_bytes(), content)
            self.assertEqual(run(f'get {name} {digest}').stdout, content)
            self.assertNotEqual(run(f'put {name} {digest}', b'overwrite').returncode, 0)
            self.assertNotEqual(run(f'delete ../other-project.txt {digest}').returncode, 0)
            self.assertNotEqual(run('put other-project.txt ' + digest, b'data').returncode, 0)
            (destination / name).write_bytes(b'tampered')
            self.assertNotEqual(run(f'delete {name} {digest}').returncode, 0)
            self.assertNotEqual(run(f'get {name} {digest}').returncode, 0)
            self.assertTrue((destination / name).exists())
            (destination / name).write_bytes(content)
            self.assertEqual(run(f'delete {name} {digest}').stdout, b'deleted\n')
            self.assertEqual(unrelated.read_text(), 'keep')


if __name__ == '__main__':
    unittest.main()
