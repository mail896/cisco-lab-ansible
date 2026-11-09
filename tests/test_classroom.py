"""Read-only classroom collector and shared polling cache; no LAB connections."""
import json
from pathlib import Path
import stat
import subprocess
from tempfile import TemporaryDirectory
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from dashboard import classroom, classroom_client as client


class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.root=Path(self.enterContext(TemporaryDirectory()))
        self.sys=self.root/'net';self.sys.mkdir()
        usb=self.root/'usb1/1-2';device=usb/'1-2:1.0';adapter=device/'net/lab0';adapter.mkdir(parents=True)
        (self.sys/'lab0').symlink_to(adapter)
        (adapter/'device').symlink_to(device)
        for name,value in dict(address='02:00:00:00:00:02',type='1').items():(adapter/name).write_text(value)
        for name,value in dict(idVendor='0bda',idProduct='8153',manufacturer='Realtek',product='USB Gigabit LAN').items():(usb/name).write_text(value)
        driver=self.root/'r8152';driver.mkdir();(device/'driver').symlink_to(driver)
        self.manifest=self.root/'network.json'
        self.settings=dict(version=1,host='LAB-08A',group='cisco.lab',identity=dict(
            lab=dict(interface='lab0',mac='02:00:00:00:00:02',usb=True),school=dict(mac='02:00:00:00:00:01',usb=False)))
        self.manifest.write_text(json.dumps(self.settings))
        self.enterContext(patch.object(client,'SYS',self.sys));self.enterContext(patch.object(client,'MANIFEST',self.manifest))
        self.enterContext(patch.object(client.socket,'gethostname',return_value='LAB-08A'))
        self.enterContext(patch.object(client.os,'fstat',return_value=SimpleNamespace(st_mode=stat.S_IFREG|0o600,st_uid=0,st_size=500)))
        self.users=self.enterContext(patch.object(client,'desktop_users',return_value=[]))

    def test_verified_usb_model_link_addresses_and_only_its_routes(self):
        address=[dict(address='02:00:00:00:00:02',flags=['UP','LOWER_UP'],addr_info=[
            dict(family='inet',local='192.0.2.2',prefixlen=24,scope='global'),
            dict(family='inet6',local='fe80::2',prefixlen=64,scope='link'),
            dict(family='inet6',local='2001:db8::2',prefixlen=64,scope='global',tentative=True)])]
        routes4=[dict(dst='default',gateway='203.0.113.254',dev='eno1'),
                 dict(dst='198.51.100.0/24',gateway='192.0.2.1',dev='lab0',metric=50,table=100),
                 dict(dst='192.0.2.2',dev='lab0',type='local',table='local')]
        with patch.object(client,'command',side_effect=[address,routes4,[]]) as read:
            result=client.collect('LAB-08A')
        self.assertEqual(result['state'],'up');self.assertEqual(result['hardware'],dict(manufacturer='Realtek',model='USB Gigabit LAN',usb_id='0bda:8153',driver='r8152'))
        self.assertEqual(result['ipv4'][0]['address'],'192.0.2.2/24')
        self.assertEqual(result['ipv6'][0]['scope'],'link');self.assertEqual(result['ipv6'][1]['flags'],['tentative'])
        self.assertEqual(len(result['routes4']),2);self.assertNotIn('203.0.113.254',json.dumps(result))
        self.assertEqual(read.call_args_list[0].args,('address','show','dev','lab0'))

    def test_missing_adapter_never_falls_back_to_another_interface(self):
        self.settings['identity']['lab']['mac']='02:00:00:00:00:99';self.manifest.write_text(json.dumps(self.settings))
        self.users.return_value=[dict(account='mara.mustermann',kind='iserv',locked=True)]
        with patch.object(client,'command') as read:result=client.collect('LAB-08A')
        self.assertEqual(result['state'],'missing');self.assertIsNone(result['ipv4']);read.assert_not_called()
        self.assertEqual(result['users'],self.users.return_value)

    def test_disabled_adapter_can_still_report_its_configured_addresses(self):
        with patch.object(client,'command',side_effect=[[dict(address='02:00:00:00:00:02',flags=[],addr_info=[])],[],[]]):
            result=client.collect('LAB-08A')
        self.assertEqual(result['state'],'down');self.assertIs(result['admin_up'],False);self.assertEqual(result['ipv6'],[])

    def test_host_and_manifest_identity_errors_stop_before_network_reads(self):
        with self.assertRaisesRegex(ValueError,'identity'):client.collect('LAB-08B')
        self.settings['identity']['lab']['usb']=False;self.manifest.write_text(json.dumps(self.settings))
        with patch.object(client,'command') as read,self.assertRaisesRegex(ValueError,'manifest'):client.collect('LAB-08A')
        read.assert_not_called()

    def test_multipath_only_includes_the_lab_next_hop(self):
        result=client.routes([dict(dst='203.0.113.0/24',nexthops=[dict(dev='eno1',gateway='192.0.2.1'),dict(dev='lab0',gateway='192.0.2.1')])],'lab0',4)
        self.assertEqual(len(result),1);self.assertEqual(result[0]['gateway'],'192.0.2.1')


class MonitorTests(unittest.TestCase):
    def test_visible_clients_share_one_batch_and_expire_only_on_a_new_request(self):
        entered,release,finished=threading.Event(),threading.Event(),threading.Event();now=[100.0]
        def read(host,record):entered.set();release.wait(2);return dict(state='up',ipv4=[],ipv6=[],routes4=[],routes6=[])
        probe=Mock(side_effect=read);monitor=classroom.Classroom(read=probe,enrolled=lambda:{'LAB-08A':{}},clock=lambda:now[0])
        original=monitor.refresh
        def complete():
            try:original()
            finally:finished.set()
        self.addCleanup(release.set)
        with patch.object(monitor,'refresh',side_effect=complete):
            self.assertTrue(monitor.snapshot()['refreshing']);self.assertTrue(entered.wait(1))
            for _ in range(20):monitor.snapshot()
            probe.assert_called_once();release.set();self.assertTrue(finished.wait(2))
            now[0]+=59;monitor.snapshot();probe.assert_called_once()
            now[0]+=100;probe.assert_called_once() # no timer or automatic background refresh
            finished.clear();monitor.snapshot();self.assertTrue(finished.wait(2));self.assertEqual(probe.call_count,2)
        rows=monitor.snapshot()['clients'];self.assertEqual(rows['LAB-08B']['state'],'unmanaged')
        rows['LAB-08A']['ipv4'].append('mutated');self.assertEqual(monitor.snapshot()['clients']['LAB-08A']['ipv4'],[])

    def test_all_sixteen_clients_are_read_in_one_shared_parallel_batch(self):
        from dashboard.deploy import LAB_TARGETS
        started=[];ready=threading.Event();release=threading.Event();finished=threading.Event();guard=threading.Lock()
        def read(host,record):
            with guard:
                started.append(host)
                if len(started)==16:ready.set()
            release.wait(4)
            return dict(state='down',users=[],ipv4=[],ipv6=[],routes4=[],routes6=[])
        monitor=classroom.Classroom(read=read,enrolled=lambda:{host:{} for host in LAB_TARGETS})
        refresh=monitor.refresh
        def batch():
            try:refresh()
            finally:finished.set()
        self.addCleanup(release.set)
        with patch.object(monitor,'refresh',side_effect=batch):
            try:
                self.assertEqual(monitor.snapshot()['interval_seconds'],60)
                self.assertTrue(ready.wait(3),'Clients did not start in parallel')
                for _ in range(10):self.assertTrue(monitor.snapshot()['refreshing'])
                self.assertEqual(len(started),16)
            finally:release.set()
            self.assertTrue(finished.wait(2))
        self.assertFalse(monitor.snapshot()['refreshing'])
        self.assertTrue(all(row['state']=='down' for row in monitor.snapshot()['clients'].values()))

    def test_failure_replaces_old_addresses_without_marking_link_as_off(self):
        read=Mock(return_value=dict(state='up',ipv4=[dict(address='192.0.2.2/24')]))
        monitor=classroom.Classroom(read=read,enrolled=lambda:{'LAB-08A':{}})
        monitor.refresh();read.side_effect=TimeoutError;monitor.refresh()
        row=monitor.snapshot()['clients']['LAB-08A']
        self.assertEqual(row['state'],'unreachable');self.assertNotIn('ipv4',row)
        self.assertFalse(monitor.refreshing)

    def test_bad_enrollment_is_reported_without_probes_and_unlocks(self):
        read=Mock();monitor=classroom.Classroom(read=read,enrolled=Mock(side_effect=ValueError))
        monitor.refresh();result=monitor.snapshot()
        self.assertTrue(result['error']);self.assertFalse(result['refreshing']);read.assert_not_called()

    def test_probe_is_pinned_inline_read_and_returns_a_safe_error(self):
        with patch.object(classroom,'ssh',return_value=['ssh','fixed']) as command,patch.object(classroom.subprocess,'run',return_value=subprocess.CompletedProcess([],0,json.dumps(dict(ok=False,code='manifest')))):
            result=classroom.probe('LAB-08A',{})
        self.assertEqual(result['state'],'error');self.assertIn('fehlt',result['error'])
        self.assertEqual(command.call_args.args[2][:5],['sudo','-n','/usr/bin/python3','-IB','-c'])
        self.assertEqual(command.call_args.args[2][-1],'LAB-08A')


class DesktopUserTests(unittest.TestCase):
    def test_only_local_graphical_users_are_reported_and_duplicate_sessions_merge(self):
        def session(uid,name,kind='x11',category='user',remote='no',locked='no',state='active'):
            return f'User={uid}\nName={name}\nType={kind}\nClass={category}\nRemote={remote}\nLockedHint={locked}\nState={state}'
        rows=[session(10001,'mara.mustermann',locked='yes'),
              session(10001,'mara.mustermann',kind='wayland'),
              session(10002,'jona.beispiel',locked='yes'),session(450,'cisco'),
              session(1000,'administrator',kind='tty',remote='yes'),
              session(1000,'administrator',kind='x11',remote='yes'),
              session(1000,'administrator',category='manager'),session(110,'sddm',category='greeter'),
              session(10003,'old.session',state='closing')]
        accounts={10001:SimpleNamespace(pw_name='mara.mustermann',pw_dir='/var/lib/iserv/client/home/mara.mustermann'),
                  10002:SimpleNamespace(pw_name='jona.beispiel',pw_dir='/var/lib/iserv/client/home/jona.beispiel'),
                  450:SimpleNamespace(pw_name='cisco',pw_dir='/var/lib/cisco-lab-local/cisco')}
        with patch.object(client.subprocess,'check_output',side_effect=['\n'.join(f'{n} {n} ignored' for n in range(len(rows))),'\n\n'.join(rows)]) as read,patch.object(client.pwd,'getpwuid',side_effect=accounts.__getitem__) as lookup:
            users=client.desktop_users()
        self.assertEqual(users,[dict(account='cisco',kind='local',locked=False),
                                dict(account='jona.beispiel',kind='iserv',locked=True),
                                dict(account='mara.mustermann',kind='iserv',locked=False)])
        self.assertEqual(read.call_count,2);self.assertEqual(lookup.call_count,3)

    def test_no_sessions_and_unavailable_read_are_different(self):
        with patch.object(client.subprocess,'check_output',return_value='') as read:
            self.assertEqual(client.desktop_users(),[]);read.assert_called_once()
        with patch.object(client.subprocess,'check_output',side_effect=subprocess.TimeoutExpired('loginctl',3)):
            self.assertIsNone(client.desktop_users())


if __name__=='__main__':unittest.main()
