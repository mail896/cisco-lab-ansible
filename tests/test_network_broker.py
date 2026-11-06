"""Backend/IPC security tests; all NM/IP effects are fake and files temporary."""
import copy
import grp
import json
import os
from pathlib import Path
import socket
import tempfile
import threading
import unittest
from unittest.mock import patch

from network.broker import auth
from network.broker.backend import Backend, CLEAR, CommandError, NM, IP
from network.broker.client import request
from network.broker.protocol import receive
from network.broker.service import Server
from network.dialog.forms import to_request,from_configuration
from network.security.lab_request import keyfile

IDENTITY={'school':{'uuid':'11111111-1111-4111-8111-111111111111','interface':'eno1','mac':'00:11:22:33:44:55','usb':False},
          'lab':{'uuid':'22222222-2222-4222-8222-222222222222','interface':'eth1','mac':'00:11:22:33:44:66','usb':True},
          'protected_subnets':['10.255.0.0/16']}
APPLY={'action':'apply','addresses':['192.168.0.6/29','2001:db8:192::6/64'],
       'routes':[{'destination':'172.16.0.0/29','gateway':'192.168.0.1'}]}


class FakeNM:
    def __init__(self,profiles):
        self.profiles=profiles;self.calls=[]
        self.loaded={IDENTITY['school']['uuid'],IDENTITY['lab']['uuid']}
        self.active={IDENTITY['school']['uuid']}
        self.fail_activation=False;self.corrupt_routes=False;self.route_changed=False
        self.carrier='on'
    def __call__(self,args):
        self.calls.append(args)
        if args[0]==IP:
            if 'link' in args:return ''
            if '-6' in args:return '[]'
            routes=[{'dst':'default','dev':'eno1','gateway':'10.255.0.1','metric':100},
                    {'dst':'10.255.0.0/16','dev':'eno1','metric':100}]
            if self.route_changed:routes[0]['metric']=999
            return json.dumps(routes)
        if args[0]!=NM:raise AssertionError(args)
        if '-f' in args and 'UUID' in args:
            return '\n'.join(sorted(self.active if '--active' in args else self.loaded))
        if 'device' in args and 'set' in args:return ''
        if 'WIRED-PROPERTIES.CARRIER' in args:return self.carrier+'\n'
        if '-g' in args:
            return 'auto\nno\ndisabled\nyes\neno1\n00:11:22:33:44:55\nmagic\n\n\n\n\n\n'
        if 'load' in args:
            content=Path(args[-1]).read_text()
            import configparser
            c=configparser.ConfigParser();c.read_string(content)
            self.loaded.add(c['connection']['uuid']);return ''
        if 'up' in args:
            identifier=args[args.index('uuid')+1]
            if identifier==IDENTITY['lab']['uuid']:
                if self.fail_activation:
                    self.fail_activation=False;raise CommandError('simulated activation failure')
                if self.corrupt_routes:self.route_changed=True;self.corrupt_routes=False
            self.active.add(identifier);return ''
        if 'down' in args:
            self.active.discard(args[-1]);self.route_changed=False;return ''
        if 'delete' in args:
            self.loaded.discard(args[-1]);self.active.discard(args[-1]);return ''
        if 'modify' in args:return ''
        raise AssertionError(args)


class BackendFixture(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.profiles=self.root/'profiles';self.profiles.mkdir()
        self.lab=self.profiles/'lab.nmconnection';self.lab.write_text(keyfile(CLEAR,IDENTITY));self.lab.chmod(0o600)
        self.school=self.profiles/'school.nmconnection'
        self.school.write_text('[connection]\nuuid='+IDENTITY['school']['uuid']+'\n');self.school.chmod(0o600)
        self.hardware=[dict(interface=v['interface'],current_mac=v['mac'],usb=v['usb'],type='1',wireless=False)
                       for v in IDENTITY.values() if isinstance(v,dict)]
        self.nm=FakeNM(self.profiles)
        self.backend=Backend(IDENTITY,self.profiles,self.root/'state',self.root/'backups',self.root/'operation.lock',
                             self.nm,lambda:self.hardware)
    def tearDown(self):self.tmp.cleanup()


class BackendTests(BackendFixture):
    def test_apply_status_clear_and_school_untouched(self):
        before=self.school.read_bytes()
        self.backend.apply(APPLY,1001)
        self.assertEqual(self.backend.status()['configuration'],APPLY)
        self.backend.apply(CLEAR,1001)
        self.assertEqual(self.backend.status()['configuration'],CLEAR)
        self.assertEqual(before,self.school.read_bytes())
        self.assertTrue(all(IDENTITY['school']['uuid'] not in call for call in self.nm.calls
                            if any(op in call for op in ('up','down','delete','modify'))))
    def test_activation_failure_restores_file_and_leaves_school_active(self):
        before=self.lab.read_bytes();self.nm.fail_activation=True
        with self.assertRaises(CommandError):self.backend.apply(APPLY,1001)
        self.assertEqual(before,self.lab.read_bytes())
        self.assertIn(IDENTITY['school']['uuid'],self.nm.active)
        self.assertNotIn(IDENTITY['lab']['uuid'],self.nm.active)
        results=[json.loads(p.read_text())['result'] for p in (self.root/'backups').glob('*/result.json')]
        self.assertEqual(results,['rolled-back'])
    def test_route_guard_causes_rollback(self):
        before=self.lab.read_bytes();self.nm.corrupt_routes=True
        with self.assertRaises(ValueError):self.backend.apply(APPLY,1001)
        self.assertEqual(before,self.lab.read_bytes())
        self.assertFalse(self.nm.route_changed)
    def test_renamed_usb_adapter_uses_mac_bus_not_prefix(self):
        self.hardware[1]['interface']='enp0s20f0u2'
        self.backend.apply(APPLY,1001)
        self.assertIn('interface-name=enp0s20f0u2',self.lab.read_text())
    def test_missing_ambiguous_or_wrong_bus_causes_no_mutation(self):
        for kind in ('missing','ambiguous','wrong-bus'):
            original=copy.deepcopy(self.hardware)
            if kind=='missing':self.hardware.pop()
            if kind=='ambiguous':self.hardware.append(self.hardware[1].copy())
            if kind=='wrong-bus':self.hardware[1]['usb']=False
            before=self.lab.read_bytes();self.nm.calls=[]
            with self.assertRaises(ValueError):self.backend.apply(APPLY,1001)
            self.assertEqual(before,self.lab.read_bytes());self.assertEqual(self.nm.calls,[])
            self.hardware=original
    def test_new_login_resets_once_and_heal_preserves_exercise(self):
        self.backend.ensure_session('boot:1:1001',1001)
        self.backend.apply(APPLY,1001)
        self.backend.ensure_session('boot:1:1001',1001)
        self.backend.heal()
        self.assertEqual(self.backend.status()['configuration'],APPLY)
        self.backend.ensure_session('boot:2:1001',1001)
        self.assertEqual(self.backend.status()['configuration'],CLEAR)
    def test_heal_recreates_deleted_lab_but_never_changes_existing_exercise(self):
        self.lab.unlink();self.nm.loaded.discard(IDENTITY['lab']['uuid'])
        self.backend.heal();self.assertTrue(self.backend.profile_path('lab').exists())
        self.backend.apply(APPLY,1001);before=self.backend.profile_path('lab').read_bytes()
        self.backend.heal();self.assertEqual(before,self.backend.profile_path('lab').read_bytes())

    def test_heal_reactivates_confirmed_exercise_after_usb_reconnect(self):
        self.backend.apply(APPLY, 1001)
        before = self.lab.read_bytes()
        lab_uuid = IDENTITY['lab']['uuid']
        self.nm.active.discard(lab_uuid)
        self.nm.carrier = 'off'
        self.backend.heal()
        self.assertNotIn(lab_uuid, self.nm.active)
        self.nm.carrier = 'on'
        self.backend.heal()
        self.assertIn(lab_uuid, self.nm.active)
        self.assertEqual(before, self.lab.read_bytes())
        self.assertEqual(APPLY, self.backend.status()['configuration'])

    def test_heal_does_not_activate_unconfirmed_profile(self):
        self.backend.heal()
        self.assertNotIn(IDENTITY['lab']['uuid'], self.nm.active)
    def test_neutral_profile_is_saved_until_carrier_returns(self):
        self.lab.unlink()
        identifier = IDENTITY['lab']['uuid']
        self.nm.loaded.discard(identifier)
        self.nm.carrier = 'off'
        self.backend.heal()
        self.assertTrue(self.backend.profile_path('lab').exists())
        self.assertNotIn(identifier, self.nm.active)
        self.nm.carrier = 'on'
        self.backend.heal()
        self.assertIn(identifier, self.nm.active)

    def test_installer_school_profile_without_extension(self):
        installer_profile = self.profiles / 'Wired connection 1'
        self.school.rename(installer_profile)
        self.assertEqual(installer_profile, self.backend.profile_path('school'))

    def test_loaded_profile_without_file_and_duplicate_uuid_fail_closed(self):
        self.lab.unlink()
        with self.assertRaises(ValueError):self.backend.apply(APPLY,1001)
        self.lab.write_text(keyfile(CLEAR,IDENTITY));self.lab.chmod(0o600)
        extra=self.profiles/'duplicate.nmconnection';extra.write_bytes(self.lab.read_bytes());extra.chmod(0o600)
        with self.assertRaises(ValueError):self.backend.apply(APPLY,1001)
    def test_invalid_request_makes_no_system_call(self):
        req=copy.deepcopy(APPLY);req['uuid']=IDENTITY['school']['uuid']
        with self.assertRaises(ValueError):self.backend.apply(req,1001)
        self.assertEqual(self.nm.calls,[])


class AuthTests(unittest.TestCase):
    def test_group_local_session_uid_and_ambiguity(self):
        peer=auth.Peer(123,1001,100,frozenset([100,555]))
        sessions=[{'id':'1','uid':1001,'name':'student'}]
        self.assertEqual(auth.authorize(peer,555,sessions)['id'],'1')
        with self.assertRaises(auth.Denied):
            auth.authorize(auth.Peer(1,1001,555,frozenset([555]),'remote-session'),555,sessions)
        for bad_peer,bad_sessions in [(auth.Peer(1,0,555,frozenset([555])),sessions),
                                      (auth.Peer(1,1001,100,frozenset([100])),sessions),
                                      (peer,[]),(peer,[{'id':'2','uid':1002,'name':'other'}]),
                                      (peer,sessions*2)]:
            with self.assertRaises(auth.Denied):auth.authorize(bad_peer,555,bad_sessions)
    def test_remote_inactive_greeter_and_no_seat_filtered(self):
        cases=[('yes','yes','seat0','user','x11'),('no','no','seat0','user','x11'),
               ('no','yes','','user','x11'),('no','yes','seat0','greeter','x11'),
               ('no','yes','seat0','user','tty')]
        for remote,active,seat,cls,kind in cases:
            def command(args):
                if 'list-sessions' in args:return '1 1001 student\n'
                return f'User=1001\nName=student\nType={kind}\nClass={cls}\nRemote={remote}\nActive={active}\nSeat={seat}\n'
            self.assertEqual(auth.local_sessions(command),[])
    def test_group_id_remapping_is_rejected(self):
        server=object.__new__(Server);server.gid=555
        server.sessions=lambda:[{'id':'1','uid':1001,'name':'student'}]
        peer=auth.Peer(123,1001,555,frozenset([555]))
        with patch('network.broker.auth.peer_credentials',return_value=peer), patch('network.broker.auth.group_id',return_value=556):
            with self.assertRaises(auth.Denied):server._authenticate(None)

    def test_real_kernel_peer_credentials(self):
        a,b=socket.socketpair()
        try:
            peer=auth.peer_credentials(a)
            self.assertEqual(peer.uid,os.getuid());self.assertEqual(peer.pid,os.getpid())
        finally:a.close();b.close()


@unittest.skipUnless(os.geteuid()==0,'IPC positive-path fake root daemon requires root test environment')
class IPCTests(BackendFixture):
    # Reuse the temporary backend; no live NetworkManager is ever contacted.
    def serve(self,authenticate=None):
        session={'id':'unit','uid':1001,'name':'student'}
        if authenticate is None:
            authenticate=lambda conn:(auth.Peer(123,1001,555,frozenset([555])),session)
        server=Server(self.root/'control.sock',self.backend,555,authenticate=authenticate,
                      boot_id='unit-boot',housekeeping=False)
        thread=threading.Thread(target=server.serve_forever,kwargs={'poll_interval':0.01},daemon=True);thread.start()
        self.addCleanup(lambda:(server.shutdown(),thread.join(),server.server_close()))
        return server
    def test_connected_client_status_apply_and_clear(self):
        self.serve();path=str(self.root/'control.sock')
        self.assertTrue(request({'action':'status'},path)['ok'])
        self.assertEqual(request(APPLY,path)['configuration'],APPLY)
        self.assertEqual(request(CLEAR,path)['configuration'],CLEAR)
    def test_fake_identity_in_json_rejected_before_reset(self):
        self.serve();path=str(self.root/'control.sock')
        bad=copy.deepcopy(APPLY);bad['uid']=0
        with self.assertRaises(ValueError):request(bad,path)
        self.assertEqual(self.nm.calls,[])
    def test_auth_denial_makes_no_backend_call(self):
        def deny(connection):raise auth.Denied('denied')
        self.serve(deny)
        with self.assertRaises(ValueError):request({'action':'status'},str(self.root/'control.sock'))
        self.assertEqual(self.nm.calls,[])


class FormTests(unittest.TestCase):
    def test_roundtrip_and_no_free_targets(self):
        self.assertEqual(to_request(*from_configuration(APPLY)),APPLY)
        with self.assertRaises(ValueError):to_request('192.168.0.6/29','nmcli connection delete LAN-Schule')
    def test_oversized_and_duplicate_frames(self):
        for data in (b'x'*16385+b'\n',b'{"action":"status","action":"clear"}\n',b'{}\n{}\n'):
            a,b=socket.socketpair()
            try:
                a.settimeout(0.2);b.sendall(data)
                with self.assertRaises(ValueError):receive(a)
            finally:a.close();b.close()


if __name__=='__main__':unittest.main()

class StorageLimitTests(BackendFixture):
    def test_insufficient_space_makes_no_network_mutation(self):
        from collections import namedtuple
        Usage=namedtuple('Usage','total used free')
        with patch('network.broker.backend.shutil.disk_usage',return_value=Usage(1000,999,1)):
            with self.assertRaises(ValueError):self.backend.apply(APPLY,1001)
        self.assertFalse(any('up' in call or 'down' in call or 'load' in call for call in self.nm.calls))
    def test_unchanged_apply_does_not_accumulate_backups_or_restart_connection(self):
        self.backend.apply(APPLY,1001)
        count=len(list((self.root/'backups').iterdir()));self.nm.calls=[]
        self.backend.apply(APPLY,1001)
        self.assertEqual(len(list((self.root/'backups').iterdir())),count)
        self.assertFalse(any('up' in call or 'down' in call or 'load' in call for call in self.nm.calls))
