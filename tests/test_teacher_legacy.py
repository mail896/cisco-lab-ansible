"""Legacy removal must preserve the live APP receiver, backups and other services."""
from contextlib import nullcontext, redirect_stdout
import io
import json
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from deployment.teacher import retire_legacy as legacy

NGINX = '''# configuration file /etc/nginx/nginx.conf:
# configuration file /etc/nginx/mime.types:
# configuration file /etc/nginx/conf.d/cisco-lab-dashboard.conf:
listen 198.51.100.53:443 ssl;
server_name teacher.example.org;
proxy_pass http://127.0.0.1:8765;
# configuration file /etc/nginx/sites-enabled/default:
'''


class LegacyTests(unittest.TestCase):
    def test_nginx_foreign_site_listener_upstream_or_include_stops_removal(self):
        legacy.nginx_scope(NGINX)
        for extra in ('listen 80;', 'server_name other.example;', 'proxy_pass http://127.0.0.1:3000;',
                      '# configuration file /etc/nginx/conf.d/other.conf:'):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                legacy.nginx_scope(NGINX+extra+'\n')

    def test_apt_cannot_remove_workstation_packages_or_install_replacements(self):
        legacy.package_plan('Purg ansible [1]\nRemv nginx [1]\n', ['ansible', 'nginx'])
        for value in ('Remv veyon-service [1]\n', 'Remv python3 [1]\n', 'Inst other (1)\n'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                legacy.package_plan(value, ['ansible', 'nginx'])

    def test_mounted_subtree_or_redirected_project_is_never_removed(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root/'project'; project.mkdir()
            with self.assertRaisesRegex(ValueError, 'Eingehängtes'):
                legacy.validate_tree(project, [project/'mounted'])
            other = root/'other'; other.mkdir()
            link = root/'link'; link.symlink_to(other, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, 'Link'):
                legacy.validate_tree(link, [])

    def test_apply_removes_only_legacy_and_retains_archives_without_copying_project(self):
        self.run_fixture(apply=True)

    def test_preview_does_not_stop_services_or_write_fallback(self):
        self.run_fixture(apply=False)

    def test_apply_accepts_confirmed_administrator_semaphore_paths_and_preserves_neighbours(self):
        self.run_fixture(apply=True, semaphore_root='/home/administrator/semaphore')

    def test_all_additional_retired_dashboard_configs_are_saved_and_removed(self):
        self.run_fixture(apply=True, additional_dropins=True)

    def run_fixture(self, *, apply, semaphore_root='/etc/semaphore', additional_dropins=False):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            def path(value):
                value = Path(value)
                return root/str(value).lstrip('/') if value.is_absolute() else value
            project = path(legacy.PROJECT); project.mkdir(parents=True)
            (project/'large.deb').write_text('never copy this')
            state = path(legacy.OLD_STATE); (state/'backup').mkdir(parents=True)
            (state/'backup/archive.gpg').write_text('archive')
            (state/'old.json').write_text('{}')
            units = path(legacy.USER_SYSTEMD); units.mkdir(parents=True)
            for name in legacy.USER_UNITS:
                (units/name).write_text('retired unit')
            drops = units/'cisco-lab-dashboard.service.d'
            if additional_dropins:
                drops.mkdir()
                for name in ('backup.conf', 'hardening.conf', 'override.conf'):
                    (drops/name).write_text('old config: '+name)
            old = [path(item) for item in legacy.OLD_FILES]
            for item in old:
                item.parent.mkdir(parents=True, exist_ok=True); item.write_text('old')
            config = path(semaphore_root+'/config.json'); config.parent.mkdir(parents=True)
            database = semaphore_root+'/db' if semaphore_root.startswith('/home/') else '/var/lib/semaphore/db'
            config.write_text(json.dumps({'bolt': {'host': database}}))
            db = path(database); db.parent.mkdir(parents=True, exist_ok=True); db.write_text('old DB')
            neighbour = path('/home/administrator/personal.txt'); neighbour.parent.mkdir(parents=True, exist_ok=True)
            neighbour.write_text('retain personal file')
            old[1].write_text('ExecStart=/usr/bin/semaphore server --config '+semaphore_root+'/config.json\n')
            receiver = path('/usr/local/lib/cisco-lab-teacher/receiver.py')
            receiver.parent.mkdir(parents=True); receiver.write_text('retain')
            path('/var/backups').mkdir(parents=True)
            calls = []
            def command(*args, **kwargs):
                calls.append(args)
                result = ''
                if args[0] == 'dpkg-query': result = 'ansible\tinstalled\nnginx\tinstalled\nsemaphore\tinstalled\n'
                elif args[0] == 'nginx': result = NGINX
                elif args[0] == 'findmnt': result = '/\n'
                elif 'show' in args:
                    result = 'masked\n' if args[-3] == 'LoadState' and args[2].endswith('.target') else 'inactive\n'
                    if 'LoadState' in args and args[2].endswith('.service'): result = 'loaded\n'
                return SimpleNamespace(stdout=result, returncode=0)
            # Keep command arguments portable: LoadState appears after -p.
            def read(*args, **kwargs):
                value = command(*args, **kwargs)
                if args[0] == 'systemctl' and 'LoadState' in args and args[2].endswith('.target'):
                    value.stdout = 'masked\n'
                return value
            argv = ['retire_legacy.py'] + (['--apply'] if apply else [])
            with patch.object(legacy, 'Path', side_effect=path), \
                    patch.object(legacy, 'PROJECT', project), patch.object(legacy, 'OLD_STATE', state), \
                    patch.object(legacy, 'USER_SYSTEMD', units), patch.object(legacy, 'OLD_FILES', old), \
                    patch.object(legacy.os, 'geteuid', return_value=0), \
                    patch.object(legacy.socket, 'gethostname', return_value='LAB-Teacher'), \
                    patch.object(legacy.pwd, 'getpwnam', return_value=SimpleNamespace(pw_uid=1006, pw_dir='/home/marc')), \
                    patch.object(legacy, 'command', side_effect=read), \
                    patch.object(legacy, 'retirement_locks', return_value=nullcontext()), \
                    patch.object(legacy, 'preserved_files', return_value={'receiver': 'unchanged'}), \
                    patch.object(legacy.subprocess, 'run', return_value=subprocess.CompletedProcess([],0)), \
                    patch('sys.argv', argv), redirect_stdout(io.StringIO()):
                legacy.main()
            self.assertEqual(receiver.read_text(), 'retain')
            self.assertEqual(neighbour.read_text(), 'retain personal file')
            self.assertEqual((state/'backup/archive.gpg').read_text(), 'archive')
            if apply:
                self.assertFalse(project.exists()); self.assertFalse((state/'old.json').exists())
                self.assertFalse(db.exists()); self.assertFalse(old[1].exists())
                copies = list(path('/var/backups').rglob('*'))
                self.assertFalse(any(item.name == 'large.deb' for item in copies))
                self.assertTrue(any(item.name == 'db' and item.read_text() == 'old DB' for item in copies if item.is_file()))
                if additional_dropins:
                    self.assertFalse(drops.exists())
                    for name in ('backup.conf', 'hardening.conf', 'override.conf'):
                        self.assertTrue(any(item.name == name and item.read_text() == 'old config: '+name
                                            for item in copies if item.is_file()))
                stopped = [item for item in calls if 'disable' in item]
                self.assertEqual(len(stopped),3)
                self.assertFalse(any('veyon' in str(item) or 'sshd' in str(item) for item in stopped))
            else:
                self.assertTrue(project.exists()); self.assertTrue(db.exists())
                self.assertFalse(list(path('/var/backups').iterdir()))
                self.assertFalse(any('disable' in item for item in calls))
