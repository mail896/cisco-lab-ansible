"""Exercise real batched Ansible transfers only in disposable local directories."""
import base64
from copy import deepcopy
from contextlib import redirect_stdout
import io
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import yaml

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('lab_files', ROOT/'library/cisco_lab_files.py')
batch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(batch)


def file_entry(path, data=b'example\n', mode='0644'):
    return dict(dest=str(path), data=base64.b64encode(data).decode(), mode=mode)


@unittest.skipUnless(os.geteuid() == 0, 'Root ownership tested with sudo in temporary paths only')
class FileBatchTests(unittest.TestCase):
    def test_binary_content_modes_ownership_and_idempotency(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            directory = root/'config'
            files = [file_entry(directory/'binary', b'\x00\xff\x80\n', '0600'),
                     file_entry(directory/'helper', b'#!/bin/sh\nexit 0\n', '0755')]
            directories = [dict(path=str(directory), mode='0700')]
            self.assertTrue(batch.install(directories, files)['changed'])
            inode = (directory/'binary').stat().st_ino
            self.assertFalse(batch.install(directories, files)['changed'])
            self.assertEqual((directory/'binary').stat().st_ino, inode)
            self.assertEqual((directory/'binary').read_bytes(), b'\x00\xff\x80\n')
            for path, mode in [(directory,0o700),(directory/'binary',0o600),(directory/'helper',0o755)]:
                info = path.stat()
                self.assertEqual((info.st_uid,info.st_gid,stat.S_IMODE(info.st_mode)), (0,0,mode))
            (directory/'helper').chmod(0o600)
            self.assertTrue(batch.install(directories, files)['changed'])
            self.assertEqual(stat.S_IMODE((directory/'helper').stat().st_mode),0o755)

    def test_check_mode_leaves_existing_and_missing_paths_untouched(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            existing = root/'existing'
            existing.write_bytes(b'original')
            result = batch.install([dict(path=str(root/'new'),mode='0700')],
                                   [file_entry(existing),file_entry(root/'new/nested/config')],check=True)
            self.assertTrue(result['changed'])
            self.assertEqual(existing.read_bytes(),b'original')
            self.assertFalse((root/'new').exists())

    def test_invalid_later_entry_is_rejected_before_any_write(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for invalid in [file_entry(root/'second',mode='invalid'),
                            dict(dest=str(root/'second'),data='not base64'),
                            file_entry(root/'first/child'),file_entry(root/'first')]:
                with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                    batch.install([], [file_entry(root/'first'),invalid])
                self.assertFalse((root/'first').exists())

    def test_symlink_targets_and_ancestors_are_not_followed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            target=root/'target'
            target.mkdir()
            link=root/'link'
            link.symlink_to(target)
            for path in [link,link/'child']:
                with self.assertRaises(ValueError): batch.install([], [file_entry(path)])
            self.assertEqual(list(target.iterdir()),[])

    def test_atomic_write_failure_preserves_original_and_removes_temporary(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            target=root/'config'
            target.write_bytes(b'original')
            with patch.object(batch.os,'replace',side_effect=OSError('synthetic replace failure')):
                with self.assertRaises(OSError): batch.install([], [file_entry(target)])
            self.assertEqual(target.read_bytes(),b'original')
            self.assertEqual(list(root.iterdir()),[target])

    def test_actual_ansible_action_sources_templates_binary_and_check_mode(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            target=root/'installed'
            image=root/'source.bin'
            image.write_bytes(bytes(range(256))*8)
            # Use real role templates and original file bytes. No client host is contacted.
            tasks=[dict(name='Bundle',cisco_lab_files=dict(
                directories=[dict(path=str(target),mode='0700')], files=[
                    dict(src=str(ROOT/'roles/lab_session/files/session_runtime.py'),
                         dest=str(target/'runtime'),mode='0755'),
                    dict(src=str(image),dest=str(target/'image'),mode='0644'),
                    dict(content="{{ lookup('ansible.builtin.template', '"+
                         str(ROOT/'roles/lab_session/templates/session.json.j2')+"') }}",
                         dest=str(target/'session.json'),mode='0644'),
                    dict(content='Grüße aus dem LAB\n',dest=str(target/'text'),mode='0600')]))]
            play=root/'test.yml'
            play.write_text(yaml.safe_dump([dict(hosts='localhost',gather_facts=False,
                vars=dict(lab_session_group='cisco.lab'), tasks=tasks)],allow_unicode=True))
            environment=dict(os.environ,ANSIBLE_ACTION_PLUGINS=str(ROOT/'action_plugins'),
                ANSIBLE_LIBRARY=str(ROOT/'library'),ANSIBLE_HOST_KEY_CHECKING='True',
                ANSIBLE_LOCAL_TEMP=str(root/'ansible-temp'),PYTHONDONTWRITEBYTECODE='1')
            command=[str(Path(sys.executable).with_name('ansible-playbook')),'-i','localhost,',
                     '-c','local','-e',json.dumps(dict(ansible_python_interpreter=sys.executable)),str(play)]
            for extra, expected in [(['--check'], 'changed=1'),([], 'changed=1'),([], 'changed=0')]:
                result=subprocess.run(command+extra,env=environment,cwd=ROOT,text=True,
                    capture_output=True,timeout=50)
                self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr)
                self.assertIn(expected,result.stdout)
                if extra: self.assertFalse(target.exists())
            self.assertEqual((target/'image').read_bytes(),image.read_bytes())
            self.assertEqual((target/'runtime').read_bytes(),
                             (ROOT/'roles/lab_session/files/session_runtime.py').read_bytes())
            self.assertEqual(json.loads((target/'session.json').read_text()),{'group':'cisco.lab'})
            self.assertEqual((target/'text').read_text(),'Grüße aus dem LAB\n')

    def test_real_role_payloads_render_and_install_together(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            image=root/'source.bin'
            image.write_bytes(b'\x00\xffTEST')
            variables={}
            for role in ['lab_serial','lab_session','lab_desktop']:
                variables.update(yaml.safe_load((ROOT/f'roles/{role}/defaults/main.yml').read_text()))
            variables.update(yaml.safe_load((ROOT/'tasks/settings-vars.yml').read_text()))
            variables.update(lab_test_playbook_dir=str(ROOT), lab_settings_files=str(ROOT/'roles/lab_desktop/files'),
                lab_desktop_image_source=str(image), lab_firefox_policy='/test/firefox/policies.json',
                lab_settings_gid=1234, lab_settings_acl={'stdout':'# synthetic ACL'},
                ansible_facts={'getent_group':{'cisco.lab':['x','1234','']}},
                lab_network_manifest={'host':'LAB-08B','group':'cisco.lab','version':1,'identity':{
                    'school':{'mac':'00:11:22:33:44:55','uuid':'11111111-1111-4111-8111-111111111111'},
                    'lab':{'mac':'00:11:22:33:44:66','uuid':'22222222-2222-4222-8222-222222222222'}}},
                lab_network_verified={'profile_paths':{'school':'/test/school','lab':'/test/lab'}},
                lab_network_old_units={'results':[]}, inventory_hostname='LAB-08B')
            for number in ['01','02','03','04','05']:
                variables['lab_block'+number+'_snapshot']={'stdout':'/test/snapshot/'+number}
            variables['lab_settings_backup']={'stdout':'/test/snapshot/settings'}
            tasks=[]
            counts={}
            sources=['roles/lab_serial/tasks/main.yml','roles/lab_session/tasks/main.yml',
                'roles/lab_desktop/tasks/main.yml','roles/lab_desktop/tasks/theme.yml',
                'roles/lab_firefox/tasks/main.yml','roles/lab_network/tasks/install.yml',
                'tasks/settings-access.yml','maintenance-client.yml']
            for name in sources:
                role_path=ROOT.joinpath(*Path(name).parts[:2]) if name.startswith('roles/') else ROOT
                def collect(items):
                    for item in items:
                        if 'cisco_lab_files' in item:
                            payload=deepcopy(item['cisco_lab_files'])
                            if isinstance(payload.get('files'),str):
                                payload['files']=deepcopy(variables['lab_settings_favorites_files'])
                            for entry in payload.get('files',[]):
                                for key in ['src','content']:
                                    if key in entry:
                                        entry[key]=entry[key].replace('playbook_dir','lab_test_playbook_dir')
                                entry['dest']=str(root/'installed')+'/'+entry['dest']
                                if 'src' in entry and not payload.get('source_root') and not entry['src'].startswith(('/','{{')):
                                    entry['src']=str(role_path/('files' if role_path!=ROOT else '')/entry['src'])
                            for entry in payload.get('directories',[]):
                                entry['path']=str(root/'installed')+'/'+entry['path']
                            tasks.append(dict(name=name, cisco_lab_files=payload,
                                              vars={'role_path':str(role_path)}))
                            counts[name]=counts.get(name,0)+1
                        for key in ['tasks','block','rescue','always']:
                            if key in item: collect(item[key])
                collect(yaml.safe_load((ROOT/name).read_text()))
            self.assertEqual(set(counts),set(sources))
            play=root/'roles.yml'
            play.write_text(yaml.safe_dump([dict(hosts='localhost',gather_facts=False,
                vars=variables,tasks=tasks)],allow_unicode=True))
            environment=dict(os.environ,ANSIBLE_ACTION_PLUGINS=str(ROOT/'action_plugins'),
                ANSIBLE_LIBRARY=str(ROOT/'library'),ANSIBLE_LOCAL_TEMP=str(root/'ansible-temp'),
                PYTHONDONTWRITEBYTECODE='1')
            command=[str(Path(sys.executable).with_name('ansible-playbook')),'-i','localhost,',
                '-c','local','-e',json.dumps(dict(ansible_python_interpreter=sys.executable)),str(play)]
            result=subprocess.run(command,env=environment,cwd=ROOT,text=True,capture_output=True,timeout=60)
            self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr)
            installed=root/'installed'
            self.assertEqual(json.loads((installed/'etc/cisco-lab/serial.json').read_text()),{'group':'cisco.lab'})
            self.assertIn('GROUP:="1234"',(installed/'etc/udev/rules.d/99-cisco-usb.rules').read_text())
            self.assertEqual((installed/'etc/cisco-lab/session-xdg/kwinrc').read_bytes(),
                             (ROOT/'roles/lab_session/files/kwinrc').read_bytes())
            self.assertEqual((installed/'usr/share/backgrounds/lab.png').read_bytes(),image.read_bytes())
            self.assertIn('match-device=mac:00:11:22:33:44:55',
                (installed/'etc/NetworkManager/conf.d/30-cisco-lab-devices.conf').read_text())
            self.assertTrue((installed/'test/snapshot/05/source/role/files/snapshot.py').is_file())
            self.assertTrue((installed/'test/snapshot/settings/restore.py').is_file())


class ConfigurationBatchTests(unittest.TestCase):
    def test_packettracer_first_use_has_a_terminal_and_later_launches_directly(self):
        source=(ROOT/'files/cisco-packettracer').read_text()
        subprocess.run(['/bin/sh','-n'],input=source,text=True,check=True)
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            tools=root/'bin'
            tools.mkdir()
            home=root/'user'
            home.mkdir()
            for name,text in [('getent',f'#!/bin/sh\nprintf "student:x:2596:100::%s:/bin/bash\\n" "{home}"\n'),
                              ('dpkg-query','#!/bin/sh\nprintf "install ok installed 9.0"\n'),
                              ('console','#!/bin/sh\nprintf "CONSOLE\\n%s\\n" "$@"\n'),
                              ('packettracer','#!/bin/sh\nprintf "APP\\n%s\\n%s\\n" "${DEBIAN_FRONTEND:-interactive}" "$@"\n')]:
                script=tools/name
                script.write_text(text)
                script.chmod(0o755)
            launcher=root/'launcher'
            launcher.write_text(source.replace('/usr/bin/konsole',str(tools/'console'))
                                .replace('/usr/bin/dpkg-query',str(tools/'dpkg-query'))
                                .replace('/etc/cisco-lab/packettracer-eula-version',str(root/'approval'))
                                .replace('/etc/cisco-lab/packettracer-9.0.defaults',str(root/'defaults'))
                                .replace('/opt/pt/packettracer.AppImage',str(tools/'packettracer')))
            environment=dict(os.environ,PATH=str(tools)+':/usr/bin:/bin')
            for accepted,expected in [(False,'CONSOLE'),(True,'APP')]:
                if accepted:
                    directory=home/'.local/.packettracer'
                    directory.mkdir(parents=True)
                    for name in ['eula-Cisco_Packet_Tracer_9.0.0','active-version']:
                        (directory/name).write_text('synthetic acceptance')
                result=subprocess.run(['/bin/sh',str(launcher),'exercise with spaces.pkt'],
                    env=environment,text=True,capture_output=True,check=True)
                self.assertEqual(result.stdout.splitlines()[0],expected)
                self.assertEqual(result.stdout.splitlines()[-1],'exercise with spaces.pkt')
            # A completely fresh RAM profile uses the operator approval without
            # copying an existing user's personal Cisco files or opening a console.
            import shutil
            shutil.rmtree(home/'.local')
            (root/'approval').write_text('9.0\n')
            (root/'defaults').write_bytes(b'neutral-multiuser-preferences')
            result=subprocess.run(['/bin/sh',str(launcher),'exercise with spaces.pkt'],
                env=environment,text=True,capture_output=True,check=True)
            self.assertEqual(result.stdout.splitlines(),['APP','noninteractive','exercise with spaces.pkt'])
            self.assertEqual((home/'pt/PT.conf').read_bytes(), b'neutral-multiuser-preferences')
            self.assertEqual((home/'pt/PT.conf').stat().st_mode & 0o777, 0o600)
            (home/'pt/PT.conf').write_bytes(b'personal-preferences')
            subprocess.run(['/bin/sh',str(launcher)],env=environment,check=True,capture_output=True)
            self.assertEqual((home/'pt/PT.conf').read_bytes(),b'personal-preferences')
            (home/'pt/PT.conf').unlink()
            (tools/'dpkg-query').write_text('#!/bin/sh\nprintf "install ok installed 9.1"\n')
            result=subprocess.run(['/bin/sh',str(launcher),'exercise with spaces.pkt'],
                env=environment,text=True,capture_output=True,check=True)
            self.assertEqual(result.stdout.splitlines()[0],'CONSOLE')
            self.assertFalse((home/'pt/PT.conf').exists())

    def test_initial_package_transaction_covers_skipped_role_prerequisites(self):
        play=yaml.safe_load((ROOT/'fresh-client.yml').read_text())[0]
        self.assertIs(play['vars']['lab_fresh_install'],True)
        initial=next(task['ansible.builtin.apt']['name'] for task in play['tasks']
                     if task['name']=='Grundpakete und Paketvoraussetzungen bereitstellen')
        def walk(items):
            for item in items:
                yield item
                for key in ['tasks','block','rescue','always']:
                    yield from walk(item.get(key,[]))
        for role in ['lab_serial','lab_session','lab_network']:
            source='install.yml' if role=='lab_network' else 'main.yml'
            for task in walk(yaml.safe_load((ROOT/f'roles/{role}/tasks/{source}').read_text())):
                if 'ansible.builtin.apt' in task:
                    self.assertLessEqual(set(task['ansible.builtin.apt']['name']),set(initial))
                    self.assertIn('not (lab_fresh_install | default(false) | bool)',task['when'])

    def veyon(self, initial, *, broken=False):
        tasks=yaml.safe_load((ROOT/'tasks/fresh/veyon.yml').read_text())
        task=next(task for task in tasks if task.get('register')=='lab_fresh_veyon_config')
        settings=[dict(key='Authentication/Method',value='1'),dict(key='Network/LocalConnectOnly',value='false')]
        current=dict(initial)
        calls=[]
        def run(args, **kwargs):
            calls.append(args)
            if args[2]=='get':
                return subprocess.CompletedProcess(args,0 if args[3] in current else 255,
                                                    current.get(args[3],'')+'\n')
            if not broken: current[args[3]]=args[4]
            return subprocess.CompletedProcess(args,0,'')
        output=io.StringIO()
        with patch('sys.stdin',io.StringIO(json.dumps(settings))),patch('subprocess.run',side_effect=run),redirect_stdout(output):
            exec(task['ansible.builtin.command']['argv'][-1],{})
        return json.loads(output.getvalue()),calls

    def test_veyon_updates_missing_values_and_preserves_matching_values(self):
        changes,calls=self.veyon({'Authentication/Method':'1'})
        self.assertEqual(changes,['Network/LocalConnectOnly'])
        self.assertEqual(len([call for call in calls if call[2]=='set']),1)
        changes,calls=self.veyon({'Authentication/Method':'1','Network/LocalConnectOnly':'false'})
        self.assertEqual(changes,[])
        self.assertFalse(any(call[2]=='set' for call in calls))

    def test_veyon_refuses_success_when_value_was_not_applied(self):
        with self.assertRaises((SystemExit,subprocess.CalledProcessError)):
            self.veyon({'Authentication/Method':'old'},broken=True)


if __name__ == '__main__': unittest.main()
