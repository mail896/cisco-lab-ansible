"""Exercise PAM substack jumps through libpam using only private test files."""
import ctypes
import ctypes.util
import os
from pathlib import Path
import pwd
from tempfile import TemporaryDirectory
import unittest

from deployment.teacher import install


class PamSubstackTests(unittest.TestCase):
    def test_real_session_stack_preserves_existing_jumps_and_skips_only_service_mount(self):
        module = Path('/usr/lib/x86_64-linux-gnu/security/pam_permit.so')
        if not module.is_file() or module.stat().st_uid != 0:
            self.skipTest('Real root-owned PAM modules required; run this test outside the filesystem sandbox')
        library = ctypes.CDLL(ctypes.util.find_library('pam'))
        if not hasattr(library, 'pam_start_confdir'):
            self.skipTest('Installed libpam lacks private configuration support')
        callback_type = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_int, ctypes.c_void_p,
                                        ctypes.c_void_p, ctypes.c_void_p)
        class Conversation(ctypes.Structure):
            _fields_ = [('conv', callback_type), ('data', ctypes.c_void_p)]
        callback = callback_type(lambda *args: 19)  # No interactive questions in a session test.
        conversation = Conversation(callback, None)
        library.pam_start_confdir.argtypes = [ctypes.c_char_p, ctypes.c_char_p,
                                            ctypes.POINTER(Conversation), ctypes.c_char_p,
                                            ctypes.POINTER(ctypes.c_void_p)]
        library.pam_start_confdir.restype = ctypes.c_int
        for name in ('pam_open_session', 'pam_close_session', 'pam_end'):
            function = getattr(library, name)
            function.argtypes = [ctypes.c_void_p, ctypes.c_int]
            function.restype = ctypes.c_int
        normal = pwd.getpwuid(os.getuid()).pw_name
        service = 'nobody' if normal != 'nobody' else 'root'
        pwd.getpwnam(service)
        cases = (
            ('direct', install.MOUNT + '\n', True, True),
            ('skip_mount', 'session [success=1 default=ignore] pam_permit.so\n' + install.MOUNT + '\n', False, False),
            ('land_on_mount', 'session [success=1 default=ignore] pam_permit.so\n'
             'session optional pam_permit.so\n' + install.MOUNT + '\n', True, True),
            ('cross_mount', 'session [success=2 default=ignore] pam_permit.so\n' + install.MOUNT
             + '\nsession optional pam_permit.so\n', False, False),
        )
        with TemporaryDirectory(prefix='lab-pam-test-') as directory:
            root = Path(directory)
            touched, tail = root / 'mount-called', root / 'tail-called'
            mount = 'session optional pam_exec.so quiet /usr/bin/touch ' + str(touched)
            tail_rule = 'session optional pam_exec.so quiet /usr/bin/touch ' + str(tail) + '\n'
            guard = install.GUARD.replace('user = ' + install.ACCOUNT, 'user = ' + service)
            (root / 'cisco-lab-teacher-mount').write_text(guard + '\n' + mount + '\n')

            def exercise(user):
                handle = ctypes.c_void_p()
                code = library.pam_start_confdir(b'lab-test', user.encode(), ctypes.byref(conversation),
                                                str(root).encode(), ctypes.byref(handle))
                self.assertEqual(code, 0)
                try:
                    for name in ('pam_open_session', 'pam_close_session'):
                        touched.unlink(missing_ok=True); tail.unlink(missing_ok=True)
                        code = getattr(library, name)(handle, 0)
                        self.assertEqual(code, 0, (name, user, code))
                        self.assertTrue(tail.exists(), 'Existing trailing session module must still run')
                        yield touched.exists()
                finally:
                    library.pam_end(handle, code)

            for name, rules, normal_mount, service_old_mount in cases:
                for converted in (False, True):
                    for user in (normal, service):
                        with self.subTest(case=name, converted=converted, user=user):
                            body = install.pam_content(rules) if converted else rules
                            body = body.replace(install.MOUNT, mount)
                            # Debian resolves relative includes under /etc/pam.d
                            # even with pam_start_confdir. Keep every test include
                            # explicitly inside our private temporary directory.
                            body = body.replace('cisco-lab-teacher-mount', str(root / 'cisco-lab-teacher-mount'))
                            (root / 'lab-test').write_text('session required pam_permit.so\n' + body + tail_rule)
                            expected = normal_mount if user == normal else (False if converted else service_old_mount)
                            self.assertEqual(list(exercise(user)), [expected, expected])

            # A numeric jump in a parent include must also keep its original target.
            for converted in (False, True):
                with self.subTest(parent_include=True, converted=converted):
                    common = install.MOUNT + '\n' + tail_rule
                    common = install.pam_content(common) if converted else common
                    common = common.replace('cisco-lab-teacher-mount', str(root / 'cisco-lab-teacher-mount'))
                    (root / 'common-fixture').write_text(common.replace(install.MOUNT, mount))
                    (root / 'lab-test').write_text('session required pam_permit.so\n'
                        'session [success=1 default=ignore] pam_permit.so\nsession include ' + str(root / 'common-fixture') + '\n')
                    self.assertEqual(list(exercise(normal)), [False, False])


if __name__ == '__main__':
    unittest.main()
