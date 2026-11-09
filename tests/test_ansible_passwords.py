"""Run both real Ansible CLI parsers locally with inherited memory passwords."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest

from dashboard.ansible_passwords import command


@unittest.skipUnless(importlib.util.find_spec('ansible'), 'Requires project Ansible environment')
class AnsiblePasswordEntryTests(unittest.TestCase):
    def test_real_ssh_askpass_works_without_ansible_on_path(self):
        with TemporaryDirectory(prefix='lab-askpass-entry-') as folder:
            root = Path(folder)
            ssh = root / 'fixture-ssh'
            ssh.write_text('#!/usr/bin/python3\nimport os,subprocess,sys\n'
                           'result=subprocess.run([os.environ["SSH_ASKPASS"],"fixture password:"],'
                           'capture_output=True,text=True)\n'
                           'if result.returncode or result.stdout.strip()!="ephemeral-ssh-fixture":\n'
                           '    print("fixture askpass unavailable",file=sys.stderr);sys.exit(255)\n'
                           'print("fixture-only-ok")\n')
            ssh.chmod(0o700)
            playbook=root/'fixture.yml'
            playbook.write_text('- hosts: fixture\n  gather_facts: false\n  tasks:\n'
                                '    - ansible.builtin.raw: fixture-only\n')
            fd = os.memfd_create('lab-password-fixture')
            try:
                os.fchmod(fd,0o600);os.write(fd,b'ephemeral-ssh-fixture')
                env = dict(os.environ,PATH='/usr/bin:/bin',ANSIBLE_HOME=str(root/'home'),
                           ANSIBLE_LOCAL_TEMP=str(root/'tmp'),ANSIBLE_LOG_PATH='',ANSIBLE_NOCOLOR='1',
                           ANSIBLE_STDOUT_CALLBACK='minimal')
                settings=json.dumps(dict(ansible_connection='ssh',ansible_user='fixture',
                        ansible_ssh_executable=str(ssh),ansible_ssh_password_mechanism='ssh_askpass',
                        ansible_ssh_args='-o ControlMaster=no -o ControlPath=none'))
                for mode,arguments in [('adhoc',['fixture','-i','fixture,','-m','raw','-a','fixture-only']),
                                       ('playbook',['-i','fixture,',str(playbook)])]:
                    with self.subTest(mode=mode):
                        cmd=command(mode,[fd],[*arguments,'-e',settings])
                        result = subprocess.run(cmd,cwd=root,env=env,pass_fds=[fd],capture_output=True,text=True,timeout=30)
                        self.assertEqual(result.returncode,0,'Real SSH askpass failed with a system-only PATH.')
                        self.assertIn('fixture-only-ok',result.stdout)
                        self.assertNotIn('ephemeral-',result.stdout+result.stderr)
            finally:
                os.close(fd)

    def test_real_adhoc_and_playbook_entries_with_anonymous_credentials(self):
        with TemporaryDirectory(prefix='lab-ansible-entry-') as folder:
            root = Path(folder)
            playbook = root / 'fixture.yml'
            playbook.write_text('- hosts: localhost\n  gather_facts: false\n  connection: local\n'
                                '  tasks:\n    - ansible.builtin.debug:\n        msg: fixture-only-ok\n')
            commands = [('adhoc', ['localhost', '-i', 'localhost,', '-c', 'local',
                                    '-m', 'ansible.builtin.debug', '-a', 'msg=fixture-only-ok']),
                        ('playbook', ['-i', 'localhost,', str(playbook)])]
            for mode, arguments in commands:
                descriptors = []
                try:
                    for value in ('ephemeral-ssh-fixture', 'ephemeral-sudo-fixture'):
                        fd = os.memfd_create('lab-password-fixture'); descriptors.append(fd)
                        os.fchmod(fd, 0o600); os.write(fd, value.encode())
                    cmd = command(mode, descriptors, arguments)
                    env = dict(os.environ, ANSIBLE_HOME=str(root / 'home'), ANSIBLE_LOG_PATH='',
                               ANSIBLE_LOCAL_TEMP=str(root / 'tmp'), ANSIBLE_NOCOLOR='1')
                    result = subprocess.run(cmd, cwd=root, env=env, pass_fds=descriptors,
                                            text=True, capture_output=True, timeout=30)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn('fixture-only-ok', result.stdout)
                    self.assertNotIn('ephemeral-', result.stdout + result.stderr)
                    self.assertNotIn('--connection-password-file', cmd)
                finally:
                    for fd in descriptors: os.close(fd)


if __name__ == '__main__': unittest.main()
