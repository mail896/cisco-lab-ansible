"""Public-source boundary checks use synthetic data and disclose no secrets."""
import unittest

from deployment.prepare_public_export import public_content, public_path, validate_files
from deployment.public_profile import sanitize, validate_profile


class PublicExportTests(unittest.TestCase):
    def test_private_profile_replaces_literals_and_regexes_without_touching_source(self):
        old='10.'+'9.8.'
        profile=dict(preserve=['editor@example.org'],replace={old:'203.0.113.'},files={},forbidden=[old])
        source=(old+'4 '+old.replace('.',r'\.')+'[1-9] editor@example.org').encode()
        result=sanitize('dashboard/sample.py',source,profile)
        self.assertEqual(result,b'203.0.113.4 203\\.0\\.113\\.[1-9] editor@example.org')
        self.assertIn(old.encode(),source)
        validate_profile({'dashboard/sample.py':result},profile)

    def test_public_profile_rejects_new_private_addresses_and_preserves_standard_ranges(self):
        with self.assertRaises(ValueError):
            validate_profile({'sample.py':('10.'+'9.8.7').encode()})
        validate_profile({'sample.py':b'10.0.0.0/8 172.16.0.0/12 192.168.0.0/16 127.0.0.1 203.0.113.4'})

    def test_operational_and_sensitive_paths_never_enter_candidate(self):
        for name in ('AGENTS.md', 'Project.State.md', 'Project.Review.md', 'HANDOVER.txt', 'inventory.ini',
                     'docs/projektstatus.txt', 'docs/montag-raumaufnahme.txt', 'deployment/teacher/PLAN.txt',
                     'local-private/settings.json', 'dashboard/.ssh/key', 'roles/demo/password.key',
                     '.checks/example.json', '../README.md', '/README.md',
                     'deployment/apps/prepare_oidc.py', 'deployment/apps/retire_local_login.py',
                     'deployment/apps/build_runtime.py', 'deployment/apps/check_teacher_setup.py',
                     'deployment/apps/import_packettracer.py', 'deployment/apps/enable_apache_route.py'):
            with self.subTest(path=name): self.assertFalse(public_path(name))
        self.assertTrue(public_path('dashboard/intake.py'))
        self.assertTrue(public_path('action_plugins/cisco_lab_files.py'))
        self.assertTrue(public_path('library/cisco_lab_files.py'))
        self.assertTrue(public_path('docs/media/dashboard-room.jpg'))

    def test_secret_values_are_rejected_without_being_echoed(self):
        secret = b'-----BEGIN '+b'OPENSSH PRIVATE KEY-----\nexample-sensitive-content'
        with self.assertRaises(ValueError) as error:
            validate_files({'dashboard/example.py': secret})
        self.assertIn('dashboard/example.py', str(error.exception))
        self.assertNotIn('example-sensitive-content', str(error.exception))

    def test_missing_or_private_document_link_blocks_export(self):
        with self.assertRaises(ValueError):
            validate_files({'README.md': b'[private](docs/projektstatus.txt)'})
        validate_files({'README.md': b'[guide](docs/dashboard.md#start)',
                        'docs/dashboard.md': b'[home](../README.md)'})

    def test_operational_status_link_is_removed_without_overwriting_release_facts(self):
        content = ('Stand: 08.11.2026. Der lokale [Projektstatus](projektstatus.txt) unterscheidet\n'
                   'fertige Funktionen, offene technische Arbeiten und ausstehende Praxistests.').encode()
        exported = public_content('docs/dashboard.md', content)
        self.assertNotIn(b'projektstatus.txt', exported)
        self.assertIn(b'ausstehende Praxistests', exported)
        self.assertEqual(public_content('docs/dashboard.md', exported), exported)
        accepted='Stand: 09.11.2026. Die Praxisabnahme ist abgeschlossen.'.encode()
        self.assertEqual(public_content('docs/dashboard.md',accepted),accepted)


if __name__ == '__main__': unittest.main()
