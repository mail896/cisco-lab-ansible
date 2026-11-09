"""Software workflow with local catalogues and simulated clients; never installs."""
from datetime import datetime, timedelta
import ast
import json
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, Mock, patch

import yaml
from dashboard import software, software_catalog, software_client as client
from maintenance.preview import BERLIN


class ImmediateThread:
    def __init__(self, target, args=(), **kwargs):
        self.target, self.args = target, args
    def start(self):
        self.target(*self.args)


class SoftwareTests(unittest.TestCase):
    def test_inventory_reads_all_selected_clients_in_one_parallel_batch(self):
        from dashboard.deploy import LAB_TARGETS
        started=[];ready=threading.Event();release=threading.Event()
        def inventory(host):
            with self.manager.guard:
                started.append(host)
                if len(started)==16:ready.set()
            release.wait(4)
            return {'filezilla':'3.0'}
        with patch.object(self.manager,'enrolled',return_value={host:{} for host in LAB_TARGETS}), \
             patch.object(self.manager,'read_inventory',side_effect=inventory):
            try:
                self.assertTrue(self.manager.refresh_inventory(list(LAB_TARGETS)))
                self.assertTrue(ready.wait(3),'Inventory still runs in serial two-client batches')
                self.assertFalse(self.manager.refresh_inventory(list(LAB_TARGETS)))
            finally:
                release.set()
                with self.manager.inventory_guard:
                    pass
        self.assertEqual(len(self.manager.inventory_state()),16)
        self.assertTrue(all(not row['refreshing'] for row in self.manager.inventory_state().values()))

    def test_full_room_dependency_plan_is_readable_without_relaxing_hardware_limit(self):
        from dashboard.admission import read_settings
        from dashboard.deploy import LAB_TARGETS
        result=dict(host='LAB-08A',packages=['pkg-0001'],changes=[dict(name='lib-'+str(i),action='install',version='1.0') for i in range(100)],
                    already=[],removals=[],digest='a'*64)
        run=dict(id='e'*32,kind='plan',status='succeeded',started=software.Software.now(),finished=software.Software.now(),
                 targets=list(LAB_TARGETS),packages=['pkg-0001'],plans={host:dict(result,host=host) for host in LAB_TARGETS},
                 results={host:dict(result,host=host) for host in LAB_TARGETS})
        self.manager.save(run)
        path=self.root/'runs'/(run['id']+'.json')
        self.assertGreater(path.stat().st_size,16384)
        self.assertEqual(self.manager.record(run['id']),run)
        self.assertEqual(len(self.manager.state()['runs'][0]['results']),16)
        self.assertNotIn('plans',self.manager.state()['runs'][0])
        with self.assertRaisesRegex(ValueError,'Hardwaredatei'):read_settings(path)
        path.chmod(0o644)
        with self.assertRaisesRegex(ValueError,'Software-Auftragsdatei'):self.manager.record(run['id'])

    def test_all_sixteen_clients_start_together_and_publish_active_progress(self):
        from dashboard.deploy import LAB_TARGETS
        from time import monotonic,sleep
        started=[];ready=threading.Event();release=threading.Event()
        def plan(host,run):
            with self.manager.guard:
                started.append(host)
                if len(started)==16:ready.set()
            release.wait(5)
            return dict(host=host,packages=run['packages'],changes=[],already=[],removals=[],digest='a'*64)
        with patch.object(self.manager,'enrolled',return_value={host:dict(known_hosts='/fixture/pin') for host in LAB_TARGETS}), \
                patch.object(self.manager,'on_host',side_effect=plan):
            try:
                reply=self.manager.create_plan(['pkg-0001'],list(LAB_TARGETS))
                self.assertTrue(ready.wait(3),'Not all clients started concurrently')
                run=self.manager.record(reply['id'])
                self.assertEqual(len(run['active']),16)
                self.assertEqual(run['results'],{})
            finally:
                release.set()
                deadline=monotonic()+3
                while self.lock.locked() and monotonic()<deadline:sleep(.01)
        self.assertFalse(self.lock.locked())
        run=self.manager.record(reply['id'])
        self.assertEqual(run['status'],'succeeded');self.assertEqual(run['active'],[])
        self.assertEqual(len(run['results']),16)

    def test_install_reuses_pinned_ssh_and_client_planner_without_local_ansible(self):
        run=dict(id='f'*32,kind='install',packages=['pkg-0001'],plans={'LAB-08A':dict(digest='a'*64)})
        answer=dict(installed=['pkg-0001'],changed=True,window_shown=True)
        with patch('dashboard.provision.ssh_command',return_value=['ssh','pinned']) as ssh, \
                patch.object(software.subprocess,'run',return_value=subprocess.CompletedProcess([],0,json.dumps(answer))) as execute:
            self.assertEqual(self.manager.on_host('LAB-08A',run),answer)
            command=ssh.call_args.args[2]
            self.assertEqual(command[:5],['sudo','-n','/usr/bin/python3','-B','-c'])
            self.assertEqual(command[-1],'install');self.assertIn('def install(',command[-2])
            self.assertEqual(json.loads(execute.call_args.kwargs['input']),dict(host='LAB-08A',packages=['pkg-0001'],expected='a'*64))
            execute.return_value=subprocess.CompletedProcess([],1,json.dumps(dict(error='Paketstand geändert')))
            with self.assertRaisesRegex(ValueError,'Paketstand geändert'):self.manager.on_host('LAB-08A',run)
            execute.return_value=subprocess.CompletedProcess([],0,json.dumps(dict(installed=[],changed=True)))
            with self.assertRaisesRegex(ValueError,'Installationsbestätigung'):self.manager.on_host('LAB-08A',run)

    def setUp(self):
        self.root = Path(self.enterContext(TemporaryDirectory()))
        self.lock = threading.Lock()
        self.manager = software.Software(self.root, SimpleNamespace(lock=self.lock))
        rows = [[f'pkg-{n:04d}','1.0','Network software','net',1024,'main'] for n in range(1001)]
        software_catalog.build(self.root/'catalog.sqlite',rows+[dict(source_host='LAB-08A')])
        self.enterContext(patch.object(self.manager,'enrolled',return_value={'LAB-08A':dict(known_hosts='/fixture/pin')}))
        self.enterContext(patch.object(software,'reserved',return_value=False))

    def profile(self):
        return self.manager.save_profile('','Unterricht',['pkg-0001'],False)

    def test_teacher_uses_shared_profiles_plans_inventory_and_scoped_logs_without_student_ssh(self):
        host='LAB-Teacher';profile=self.profile()
        with patch.object(self.manager.teacher,'state',return_value=dict(ready=True)):
            self.assertIn(host,self.manager.state()['hosts'])
            self.assertEqual(self.manager.targets([host,'LAB-08A']),['LAB-08A',host])
            with self.assertRaises(ValueError):self.manager.targets([host],students_only=True)
            with patch.object(software,'threading',SimpleNamespace(Thread=ImmediateThread)), \
                    patch.object(self.manager.teacher,'execute') as execute:
                answer=dict(host=host,packages=['pkg-0001'],changes=[],already=[],removals=[],digest='b'*64)
                execute.return_value=subprocess.CompletedProcess([],0,json.dumps(answer))
                plan=self.manager.create_plan(['pkg-0001'],[host],profile['id'])
                self.assertEqual(plan['status'],'succeeded')
                execute.assert_called_once_with('plan',['pkg-0001'])
                execute.return_value=subprocess.CompletedProcess([],0,json.dumps(dict(installed=['pkg-0001'],changed=False)))
                run=self.manager.start(plan['id'],True)
                self.assertEqual(run['status'],'succeeded')
                self.assertIn('Softwareprofil',self.manager.output(run['id'],host))
                self.assertEqual(execute.call_args.args,('install',['pkg-0001'],'b'*64))
            with patch.object(self.manager.teacher,'inventory',return_value='pkg-0001\tall\tinstalled\t2.0\n'):
                self.assertEqual(self.manager.read_inventory(host),{'pkg-0001':'2.0'})
        self.assertNotIn(host,self.manager.enrolled())

    def completed_plan(self, profile_id=''):
        with patch.object(software,'threading',SimpleNamespace(Thread=ImmediateThread)), patch.object(self.manager,'on_host',return_value=dict(
                host='LAB-08A',packages=['pkg-0001'],changes=[],already=[],removals=[],digest='a'*64)):
            return self.manager.create_plan(['pkg-0001'],['LAB-08A'],profile_id)

    def test_already_installed_profile_can_be_reviewed_without_reinstalling_pilot(self):
        profile=self.profile();run=self.completed_plan(profile['id'])
        self.assertEqual(run['kind'],'plan')
        approved=self.manager.review(run['id'],True)
        self.assertEqual(approved['approved'],software.package_revision(profile['packages']))
        self.assertEqual(self.manager.records()[0]['kind'],'plan')

    def test_local_fts_search_and_pagination_never_contact_clients(self):
        with patch.object(software.subprocess,'run',side_effect=AssertionError('Network search')):
            result=self.manager.search('Network',2)
            self.assertEqual(result['pagination'],dict(page=2,pages=101,total=1001,page_size=10))
            self.assertEqual(len(result['packages']),10)
            self.assertEqual(self.manager.search('pkg-0001',1)['packages'][0]['name'],'pkg-0001')
            self.assertEqual(self.manager.search('" OR DROP TABLE packages;',1)['packages'],[])
        self.assertEqual(self.manager.catalog()['count'],1001)

    def test_empty_and_short_search_never_browse_the_whole_catalog(self):
        with patch.object(software.sqlite3,'connect',side_effect=AssertionError('Empty search read catalogue')):
            for query in ('',' ','x','???'):
                self.assertEqual(self.manager.search(query,1)['pagination']['total'],0)

    def test_inventory_is_explicit_cached_and_unknown_is_not_reported_as_missing(self):
        self.assertEqual(self.manager.search('pkg-0001',1)['packages'][0]['installed'],{})
        with patch.object(software,'threading',SimpleNamespace(Thread=ImmediateThread)),patch.object(self.manager,'read_inventory',return_value={'pkg-0001':'2.0'}) as read:
            self.assertTrue(self.manager.refresh_inventory(['LAB-08A']))
            for query in ('pkg-0001','pkg-0002'):
                self.manager.search(query,1)
            read.assert_called_once_with('LAB-08A')
        self.assertEqual(self.manager.search('pkg-0001',1)['packages'][0]['installed'],{'LAB-08A':'2.0'})
        self.assertEqual(self.manager.search('pkg-0002',1)['packages'][0]['installed'],{'LAB-08A':None})
        self.assertNotIn('packages',self.manager.inventory_state()['LAB-08A'])
        with patch.object(software,'threading',SimpleNamespace(Thread=ImmediateThread)),patch.object(self.manager,'read_inventory',side_effect=ValueError('offline')):
            self.manager.refresh_inventory(['LAB-08A'])
        self.assertTrue(self.manager.inventory_state()['LAB-08A']['error'])
        self.assertEqual(self.manager.search('pkg-0001',1)['packages'][0]['installed'],{})
        self.assertFalse(self.manager.inventory_guard.locked())

    def test_inventory_read_is_fixed_dpkg_command_and_filters_architecture_and_status(self):
        data='pkg-0001\tamd64\tinstalled\t1.0\npkg-0002:i386\ti386\tinstalled\t2.0\npkg-0003\tall\tconfig-files\t3.0\n'
        with patch('dashboard.provision.ssh_command',return_value=['ssh','readonly']) as command,patch.object(software.subprocess,'run',return_value=subprocess.CompletedProcess([],0,data)):
            self.assertEqual(self.manager.read_inventory('LAB-08A'),{'pkg-0001':'1.0'})
        self.assertEqual(command.call_args.args[2][:3],['/usr/bin/dpkg-query','-W','-f'])
        self.assertNotIn('sudo',str(command.call_args))

    def test_inventory_started_before_install_cannot_restore_a_stale_cache(self):
        def changed_during_read(host):
            self.manager.invalidate_inventory([host]);return {'pkg-0001':'old'}
        with patch.object(software,'threading',SimpleNamespace(Thread=ImmediateThread)),patch.object(self.manager,'read_inventory',side_effect=changed_during_read):
            self.manager.refresh_inventory(['LAB-08A'])
        self.assertEqual(self.manager.inventory_state(),{})

    def test_profile_edit_invalidates_approval_and_baseline_requires_pilot(self):
        profile=self.profile()
        with self.assertRaisesRegex(ValueError,'Pilot'):
            self.manager.save_profile(profile['id'],'Unterricht',profile['packages'],True)
        plan=self.completed_plan(profile['id'])
        with patch.object(software,'threading',SimpleNamespace(Thread=ImmediateThread)),patch.object(self.manager,'on_host',return_value={}):
            run=self.manager.start(plan['id'],True)
        self.manager.review(run['id'],True)
        self.manager.save_profile(profile['id'],'Unterricht',profile['packages'],True)
        self.assertEqual(software.baseline_packages(self.root/'profiles'),['pkg-0001'])
        edited=self.manager.save_profile(profile['id'],'Unterricht',['pkg-0002'],False)
        self.assertFalse(edited['approved'])
        self.assertEqual(software.baseline_packages(self.root/'profiles'),[])
        self.manager.delete_profile(profile['id'])
        self.assertEqual(self.manager.profiles(),[])

    def test_export_is_standalone_yaml_and_does_not_contain_controller_credentials(self):
        profile=self.profile()
        play=yaml.safe_load(self.manager.export(profile['id']))[0]
        self.assertEqual(play['vars']['lab_software_packages'],['pkg-0001'])
        command=play['tasks'][1]['ansible.builtin.command']['argv']
        ast.parse(command[3])
        self.assertEqual(command[-1],'install')
        self.assertNotIn('private-key',str(play))

    def test_invalid_package_and_target_values_fail_before_launch(self):
        for value in (['--yes'],['pkg-*'],['pkg-0001;reboot'],['pkg-0001']*2,[],['missing-package']):
            with self.subTest(value=value),self.assertRaises(ValueError):
                self.manager.create_plan(value,['LAB-08A'])
        for hosts in (['LAB-Teacher'],['LAB-08B'],['LAB-08A','LAB-08A'],[],['apps']):
            with self.subTest(hosts=hosts),self.assertRaises(ValueError):
                self.manager.create_plan(['pkg-0001'],hosts)
        self.assertFalse(self.lock.locked());self.assertEqual(self.manager.records(),[])

    def test_install_is_explicit_single_use_and_shares_global_lock(self):
        plan=self.completed_plan()
        with self.assertRaises(ValueError): self.manager.start(plan['id'],False)
        self.lock.acquire()
        try: self.assertIsNone(self.manager.start(plan['id'],True))
        finally:self.lock.release()
        with patch.object(software,'threading',SimpleNamespace(Thread=ImmediateThread)),patch.object(self.manager,'on_host',return_value={}) as install:
            self.manager.start(plan['id'],True);install.assert_called_once()
            with self.assertRaises(ValueError):self.manager.start(plan['id'],True)
        self.assertFalse(self.lock.locked())

    def test_stale_changed_or_removing_plan_cannot_install(self):
        run=self.manager.record(self.completed_plan()['id'])
        for field,value in [('finished',(datetime.now(BERLIN)-timedelta(minutes=16)).isoformat()),('revision','old')]:
            changed=dict(run,**{field:value});self.manager.save(changed)
            with self.assertRaises(ValueError):self.manager.start(run['id'],True)
        run['plans']['LAB-08A']['removals']=['important-package'];self.manager.save(run)
        with self.assertRaisesRegex(ValueError,'entfernen'):self.manager.start(run['id'],True)

    def test_client_failure_and_restart_release_lock_and_preserve_history(self):
        with patch.object(software,'threading',SimpleNamespace(Thread=ImmediateThread)),patch.object(self.manager,'on_host',side_effect=ValueError('offline')):
            run=self.manager.create_plan(['pkg-0001'],['LAB-08A'])
        record=self.manager.record(run['id'])
        self.assertEqual(record['status'],'failed');self.assertEqual(record['results']['LAB-08A']['error'],'offline')
        self.assertFalse(self.lock.locked())
        record['status']='running';self.manager.save(record)
        software.Software(self.root,self.manager.deployments)
        self.assertEqual(self.manager.record(run['id'])['status'],'interrupted')

    def test_catalog_failure_preserves_existing_index_and_unlocks_refresh(self):
        before=(self.root/'catalog.sqlite').read_bytes()
        with patch.object(software,'threading',SimpleNamespace(Thread=ImmediateThread)),patch('dashboard.provision.ssh_command',return_value=['ssh']),patch.object(software.subprocess,'Popen',side_effect=OSError('offline')):
            self.assertTrue(self.manager.refresh_catalog())
        self.assertEqual((self.root/'catalog.sqlite').read_bytes(),before)
        self.assertFalse(self.manager.catalog_guard.locked());self.assertTrue(self.manager.catalog()['error'])

    def test_logs_are_bounded_and_scoped_to_actual_job_targets(self):
        run=self.completed_plan()
        (self.root/'runs'/(run['id']+'-LAB-08A.log')).write_bytes(b'x'*70000)
        self.assertEqual(len(self.manager.output(run['id'],'LAB-08A')),65536)
        for identity,host in [('../key','LAB-08A'),(run['id'],'LAB-08B')]:
            with self.assertRaises(ValueError):self.manager.output(identity,host)


class ClientTests(unittest.TestCase):
    def test_plan_uses_resolver_without_installing_or_updating_lists(self):
        package=SimpleNamespace(is_installed=False,candidate=SimpleNamespace(version='1.2',origins=[
            SimpleNamespace(origin='Debian',codename='trixie',trusted=True)]),mark_install=Mock(),
            name='gimp',marked_delete=False,marked_downgrade=False)
        cache=Mock();cache.__contains__=Mock(return_value=True);cache.__getitem__=Mock(return_value=package)
        cache.broken_count=0;cache.get_changes.return_value=[package];cache.required_download=123;cache.required_space=456
        apt=SimpleNamespace(Cache=Mock(return_value=cache));apt_pkg=SimpleNamespace(config=Mock())
        with patch.dict('sys.modules',apt=apt,apt_pkg=apt_pkg),patch.object(client.platform,'freedesktop_os_release',return_value=dict(ID='debian',VERSION_CODENAME='trixie')),patch.object(client.socket,'gethostname',return_value='LAB-08A'),patch.object(client.subprocess,'check_output',return_value='amd64\n') as commands:
            result=client.plan(['gimp'],'LAB-08A')
        self.assertEqual(result['changes'][0]['version'],'1.2');self.assertEqual(len(result['digest']),64)
        package.mark_install.assert_called_once();commands.assert_called_once_with(['dpkg','--print-architecture'],text=True)

    def test_changed_plan_and_removal_abort_before_progress_or_apt(self):
        for plan in (dict(removals=['critical'],digest='same'),dict(removals=[],digest='changed')):
            with patch.object(client,'plan',return_value=plan),patch.object(client.os,'geteuid',return_value=0),patch.object(client,'progress_path') as output, self.assertRaises(ValueError):
                client.install(['gimp'],'LAB-08A','same')
            output.assert_not_called()

    def test_install_has_pinned_selection_no_removal_and_error_output(self):
        plan=dict(removals=[],digest='same',packages=['gimp'],changes=[dict(name='gimp',version='1.2')])
        process=Mock();process.stdout=iter(['Installing gimp\n']);process.wait.return_value=0
        process.__enter__=Mock(return_value=process);process.__exit__=Mock(return_value=False)
        writes=[]
        with patch.object(client,'plan',return_value=plan),patch.object(client.os,'geteuid',return_value=0),patch.object(client,'progress_path',return_value=Path('/fixture')),patch.object(client,'write_progress',side_effect=lambda p,d:writes.append(dict(d))),patch.object(client,'show_progress',return_value=True),patch.object(client.subprocess,'Popen',return_value=process) as apt:
            result=client.install(['gimp'],'LAB-08A','same')
        command=apt.call_args.args[0]
        self.assertIn('--no-remove',command);self.assertIn('--no-install-recommends',command);self.assertEqual(command[-1],'gimp=1.2')
        self.assertTrue(result['window_shown']);self.assertEqual(writes[-1]['status'],'succeeded')

    def test_desktop_window_source_compiles_and_greeter_is_skipped(self):
        ast.parse(client.GUI)
        for fields in ('Type=x11\nClass=greeter\nActive=yes\nUser=110\n',
                       'Type=x11\nClass=user\nActive=yes\nUser=0\n',
                       'Type=x11\nClass=user\nActive=yes\nUser=498\nRemote=yes\n'):
            with patch.object(client.subprocess,'check_output',side_effect=['1 session\n',fields]),patch.object(client.subprocess,'Popen') as window:
                self.assertFalse(client.show_progress(Path('/fixture'),'test'))
            window.assert_not_called()

    def test_desktop_window_drops_root_and_uses_only_display_environment(self):
        for uid,name in ((1001,'demo'),(498,'cisco')):
            process=MagicMock();process.name='42';process.stat.return_value=SimpleNamespace(st_uid=uid)
            user=SimpleNamespace(pw_dir='/home/'+name,pw_name=name,pw_gid=1001)
            fields=f'Type=x11\nClass=user\nActive=yes\nUser={uid}\nLeader=42\nRemote=no\n'
            with self.subTest(uid=uid),patch.object(client.subprocess,'check_output',side_effect=['2 desktop\n',fields]),patch.object(client,'Path',return_value=process),patch.object(client.pwd,'getpwuid',return_value=user),patch.object(client.os,'getgrouplist',return_value=[1001]),patch.object(client.subprocess,'Popen') as window:
                process.iterdir.return_value=[]
                process.__truediv__=Mock(return_value=process)
                process.read_bytes.return_value=b'DISPLAY=:0\0XAUTHORITY=/home/demo/.Xauthority\0PASSWORD=never-pass-this\0'
                self.assertTrue(client.show_progress(Path('/fixture'),'test'))
            kwargs=window.call_args.kwargs
            self.assertEqual((kwargs['user'],kwargs['group'],kwargs['extra_groups']),(uid,1001,[1001]))
            self.assertEqual(kwargs['env']['DISPLAY'],':0');self.assertNotIn('PASSWORD',kwargs['env'])


if __name__=='__main__':unittest.main()
