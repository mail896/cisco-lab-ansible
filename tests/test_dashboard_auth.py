"""OIDC and role boundaries with signed local fixtures; no IServ or LAB calls."""
import base64
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from email.message import Message
import importlib.util
import io
import http.client
from http.cookies import SimpleCookie
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import time
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlencode, urlsplit

from dashboard import auth
from dashboard.server import Handler

OWNER = '11111111-1111-4111-8111-111111111111'
READER = '22222222-2222-4222-8222-222222222222'


def settings():
    return dict(version=1, issuer=auth.ISSUER, redirect_uri=auth.CALLBACK, client_id='fixture-client',
                client_secret='fixture-secret', admin_account='lab.admin', admin_uuid=OWNER,
                reader_group='Kollegium', local_login_enabled=True)


def info(account='teacher.test', uid=READER, group=True):
    return {'sub': 'fixture-subject', 'iserv:uuid': uid, 'preferred_username': account,
            'name': 'Testperson', 'iserv:groups': [{'act': 'kollegium', 'name': 'Kollegium'}] if group else []}


class RoleTests(unittest.TestCase):
    def test_admin_is_bound_to_account_and_stable_uuid(self):
        self.assertEqual(auth.identity(info('lab.admin', OWNER), settings())['role'], 'admin')
        self.assertEqual(auth.identity(info('lab.admin'), settings())['role'], 'reader')
        with self.assertRaises(auth.Denied): auth.identity(info('lab.admin', READER, False), settings())
        with self.assertRaises(auth.Denied): auth.identity(info('student.test', READER, False), settings())

    def test_verified_group_formats_and_bad_identity(self):
        for value in ([{'name': 'Kollegium'}], {'act': 'kollegium'}, {'items': [{'name': 'Kollegium'}]},
                      {'17': {'name': 'Kollegium'}}):
            data = info(); data['iserv:groups'] = value
            self.assertEqual(auth.identity(data, settings())['role'], 'reader')
        for value in ('Kollegium', None, [], [{'name': 'Kollegium Schüler'}]):
            data = info(); data['iserv:groups'] = value
            with self.assertRaises(auth.Denied): auth.identity(data, settings())
        data = info(); data['uuid'] = OWNER
        with self.assertRaises(auth.Denied): auth.identity(data, settings())

    def test_cookie_is_secure_scoped_and_duplicates_are_rejected(self):
        value = 'a'*43
        self.assertEqual(auth.cookie_value(auth.SESSION_COOKIE+'='+value, auth.SESSION_COOKIE), value)
        self.assertIsNone(auth.cookie_value(auth.SESSION_COOKIE+'='+value+'; '+auth.SESSION_COOKIE+'='+value, auth.SESSION_COOKIE))
        result = auth.cookie(auth.SESSION_COOKIE, value, 300, '/cisco-lab')
        for item in ('Secure', 'HttpOnly', 'SameSite=Lax', 'Path=/cisco-lab/'):
            self.assertIn(item, result)


@unittest.skipUnless(importlib.util.find_spec('jwt'), 'Requires project-local PyJWT')
class ProviderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import jwt
        from cryptography.hazmat.primitives.asymmetric import rsa
        cls.private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.jwk = jwt.algorithms.RSAAlgorithm.to_jwk(cls.private.public_key(), as_dict=True)
        cls.jwk.update(kid='fixture-key', use='sig', alg='RS256')

    def setUp(self):
        self.config = settings(); self.user = info(); self.claim_changes = {}; self.extra_tokens = {}
        self.calls = []; self.bad_signature = False
        self.tx = dict(nonce='fixture-nonce', verifier='fixture-verifier', state='fixture-state')
        self.provider = auth.Provider(self.config, fetch=self.fetch)

    def fetch(self, url, **kwargs):
        import jwt
        self.calls.append((url, kwargs))
        if url.endswith('openid-configuration'):
            return dict(issuer=auth.ISSUER, authorization_endpoint=auth.ISSUER+'/authorize',
                        token_endpoint=auth.ISSUER+'/token', userinfo_endpoint=auth.ISSUER+'/userinfo',
                        jwks_uri=auth.ISSUER+'/keys', id_token_signing_alg_values_supported=['RS256'])
        if url.endswith('/keys'): return dict(keys=[self.jwk])
        if url.endswith('/userinfo'): return deepcopy(self.user)
        if url.endswith('/token'):
            self.assertEqual(kwargs['data']['code_verifier'], self.tx['verifier'])
            now = int(time.time())
            claims = dict(iss=auth.ISSUER, aud=self.config['client_id'], sub='fixture-subject',
                          iat=now, exp=now+300, nonce=self.tx['nonce'], **{'iserv:groups': [{'name': 'Kollegium'}]})
            claims.update(self.claim_changes)
            token = jwt.encode(claims, self.private, algorithm='RS256', headers={'kid': 'fixture-key'})
            if self.bad_signature: token = token[:-8] + 'abcdefgh'
            return dict(id_token=token, access_token='fixture-access', token_type='Bearer', expires_in=300, **self.extra_tokens)
        raise AssertionError('Unexpected provider URL')

    def test_pkce_and_nonce_are_sent_and_client_secret_is_not_in_redirect(self):
        url = self.provider.begin(self.tx); query = parse_qs(urlsplit(url).query)
        self.assertEqual(query['nonce'], [self.tx['nonce']])
        self.assertEqual(query['code_challenge_method'], ['S256'])
        self.assertIn('iserv:groups', query['scope'][0])
        self.assertNotIn(self.config['client_secret'], url)

    def test_verified_signed_token_and_userinfo_subject_produce_reader(self):
        result = self.provider.complete('fixture-code', self.tx)
        self.assertEqual(result['identity']['role'], 'reader')
        self.assertGreater(result['expires'], time.time())

    def test_bad_nonce_issuer_audience_expiry_authorized_party_and_hash_are_rejected(self):
        for change in ({'nonce': 'wrong'}, {'iss': 'https://elsewhere.example'}, {'aud': 'other'},
                       {'exp': int(time.time())-100}, {'azp': 'other'},
                       {'aud': [self.config['client_id'], 'other']}, {'at_hash': 'wrong'}):
            self.claim_changes = change
            with self.subTest(change=change), self.assertRaises(auth.AuthError):
                self.provider.complete('fixture-code', self.tx)

    def test_bad_signature_and_mismatching_userinfo_are_rejected(self):
        self.bad_signature = True
        with self.assertRaises(auth.AuthError) as error: self.provider.complete('fixture-code', self.tx)
        self.assertNotIsInstance(error.exception, auth.Denied)
        self.bad_signature = False; self.user['sub'] = 'different'
        with self.assertRaises(auth.AuthError): self.provider.complete('fixture-code', self.tx)

    def test_empty_userinfo_group_list_cannot_be_overridden_by_id_token(self):
        self.user['iserv:groups'] = []
        with self.assertRaises(auth.Denied) as error: self.provider.complete('fixture-code', self.tx)
        self.assertEqual(error.exception.user['account'], 'teacher.test')
        self.assertEqual(error.exception.user['name'], 'Testperson')
        self.user.pop('iserv:groups')
        self.assertEqual(self.provider.complete('fixture-code', self.tx)['identity']['role'], 'reader')

    def test_provider_redirects_and_foreign_endpoints_are_not_allowed(self):
        for url in ('https://elsewhere.example/keys', 'http://school.example.org/keys',
                    'https://school.example.org@elsewhere.example/keys', 'https://school.example.org/keys#bad'):
            with self.assertRaises(auth.AuthError): self.provider.endpoint(url)
        with self.assertRaises(auth.AuthError): auth.NoRedirect().redirect_request(None, None, None, None, None, None)


class SessionAndRouteTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(TemporaryDirectory()))
        self.config = settings(); self.now = [time.time()]
        self.provider = Mock()
        self.provider.begin.side_effect = lambda tx: auth.ISSUER+'/authorize?state='+tx['state']
        self.auth = auth.Auth(self.root/'auth', self.config, self.provider, clock=lambda: self.now[0])
        self.password = self.root/'passwort'; self.password.write_text('fixture-password'); self.password.chmod(0o600)

    def login(self, role='reader'):
        data = info('lab.admin', OWNER) if role == 'admin' else info()
        self.provider.complete.return_value = dict(identity=auth.identity(data, self.config), subject='fixture',
                                                   access='fixture-token', expires=self.now[0]+300)
        tid, url = self.auth.begin(); state = parse_qs(urlsplit(url).query)['state'][0]
        return self.auth.complete(tid, urlencode(dict(state=state, code='fixture-code')))

    def handler(self, path, method='GET', sid=None, csrf=None, body=None, local=False):
        handler = Handler.__new__(Handler); handler.path = '/cisco-lab'+path; handler.command = method
        handler.client_address = ('127.0.0.1', 12345); handler.headers = Message()
        handler.headers['Host'] = '127.0.0.1:8765'
        if not local:
            handler.headers['X-Forwarded-Proto'] = 'https';handler.headers['X-Forwarded-Host'] = 'lab-app.example.org'
        if method == 'POST':
            handler.headers['Origin'] = 'http://127.0.0.1:8765' if local else 'https://lab-app.example.org'
            handler.headers['Content-Type'] = 'application/json'
        if sid: handler.headers['Cookie'] = auth.SESSION_COOKIE+'='+sid
        if csrf: handler.headers['X-CSRF-Token'] = csrf
        payload = json.dumps(body if body is not None else {}).encode()
        handler.headers['Content-Length'] = str(len(payload));handler.rfile = io.BytesIO(payload)
        handler.server = SimpleNamespace(auth=self.auth, password_path=self.password, server_port=8765,
                                         https_host='lab-app.example.org', base_path='/cisco-lab',
                                         backups=SimpleNamespace(profile='apps'), dashboard=Mock(), provision=Mock())
        handler.server.dashboard.refresh.return_value = True
        handler._send = Mock()
        return handler

    def test_image_previews_require_login_and_readers_can_view_without_actions(self):
        anonymous = self.handler('/api/appearance/preview/'+'a'*64)
        anonymous.server.appearance = Mock()
        anonymous.do_GET()
        anonymous.server.appearance.preview.assert_not_called()
        self.assertEqual(anonymous._send.call_args.args[0],401)
        sid, _ = self.login('reader')
        reader = self.handler('/api/appearance/preview/'+'a'*64,sid=sid)
        reader.server.appearance = Mock()
        reader.server.appearance.preview.return_value = b'fixture preview'
        reader.do_GET()
        reader.server.appearance.preview.assert_called_once_with('a'*64)
        self.assertEqual(reader._send.call_args.args,(200,b'fixture preview','image/jpeg'))
        reader.server.appearance.preview.side_effect = FileNotFoundError
        reader.do_GET()
        self.assertEqual(reader._send.call_args.args[0],404)

    def test_activity_requires_login_and_is_readable_without_admin_rights(self):
        anonymous = self.handler('/api/activity')
        anonymous.server.activity = Mock()
        anonymous.do_GET()
        anonymous.server.activity.snapshot.assert_not_called()
        self.assertEqual(anonymous._send.call_args.args[0], 401)
        sid, _ = self.login('reader')
        reader = self.handler('/api/activity', sid=sid)
        reader.server.activity = Mock()
        reader.server.activity.snapshot.return_value = dict(busy=False)
        reader.do_GET()
        self.assertEqual(reader._send.call_args.args[0], 200)
        reader.server.activity.snapshot.assert_called_once_with()

    def test_image_upload_requires_admin_csrf_size_and_operation_lock(self):
        from urllib.parse import quote
        sid,session=self.login('admin')
        def upload(csrf=session['csrf'],length=7):
            handler=self.handler('/api/appearance/upload','POST',sid,csrf)
            handler.headers.replace_header('Content-Type','application/octet-stream')
            handler.headers.replace_header('Content-Length',str(length))
            handler.headers['X-LAB-Image-Name']=quote('Bild ä.png')
            handler.rfile=io.BytesIO(b'fixture')
            handler.server.appearance=Mock();handler.server.appearance.upload.return_value=dict(uploaded='Bild ä.png')
            handler.server.deployments=SimpleNamespace(lock=Mock())
            handler.server.deployments.lock.acquire.return_value=True
            return handler
        accepted=upload();accepted.do_POST()
        self.assertEqual(accepted._send.call_args.args[0],201)
        accepted.server.appearance.upload.assert_called_once_with('Bild ä.png',b'fixture')
        accepted.server.deployments.lock.release.assert_called_once()
        for handler,status in ((upload(csrf='wrong'),403),(upload(length=48*1024*1024+1),413)):
            handler.do_POST();self.assertEqual(handler._send.call_args.args[0],status)
            handler.server.appearance.upload.assert_not_called()
        busy=upload();busy.server.deployments.lock.acquire.return_value=False;busy.do_POST()
        self.assertEqual(busy._send.call_args.args[0],409);busy.server.appearance.upload.assert_not_called()
        failed=upload();failed.server.appearance.upload.side_effect=ValueError('Ungültiges Bild.');failed.do_POST()
        self.assertEqual(failed._send.call_args.args[0],400);failed.server.deployments.lock.release.assert_called_once()

    def test_state_is_browser_bound_single_use_and_denial_creates_no_session(self):
        tid, url = self.auth.begin(); state = parse_qs(urlsplit(url).query)['state'][0]
        with self.assertRaises(auth.AuthError): self.auth.complete('x'*43, urlencode(dict(state=state, code='fixture')))
        self.provider.complete.assert_not_called()
        with self.assertRaises(auth.Denied): self.auth.complete(tid, urlencode(dict(state=state, error='access_denied')))
        self.assertFalse(self.auth.sessions)
        with self.assertRaises(auth.AuthError): self.auth.complete(tid, urlencode(dict(state=state, code='fixture')))

    def test_expiry_logout_and_revoked_group_remove_access(self):
        sid, session = self.login(); self.assertIs(self.auth.lookup(sid), session)
        self.auth.logout(sid); self.assertIsNone(self.auth.lookup(sid))
        sid, session = self.login(); self.now[0] += 301; self.assertIsNone(self.auth.lookup(sid))
        sid, session = self.login(); self.now[0] += 61; self.provider.refresh.side_effect = auth.Denied()
        with self.assertRaises(auth.Denied): self.auth.lookup(sid)
        self.assertIsNone(self.auth.lookup(sid))

    def test_slow_session_check_leaves_other_sessions_and_logout_responsive(self):
        slow_sid, slow_session = self.login()
        other_sid, other_session = self.login()
        slow_session['checked'] = self.now[0] - 61
        entered, release = threading.Event(), threading.Event()

        def refresh(session):
            entered.set()
            if not release.wait(3):
                raise AssertionError('Refresh was not released')
            return session['identity']

        self.provider.refresh.side_effect = refresh
        with ThreadPoolExecutor(max_workers=3) as workers:
            pending = workers.submit(self.auth.lookup, slow_sid)
            try:
                self.assertTrue(entered.wait(1))
                self.assertIs(workers.submit(self.auth.lookup, other_sid).result(timeout=1), other_session)
                workers.submit(self.auth.logout, slow_sid).result(timeout=1)
            finally:
                release.set()
            self.assertIsNone(pending.result(timeout=1))
        self.assertIsNone(self.auth.lookup(slow_sid))

    def test_parallel_requests_share_one_check_and_expiry_during_check_is_enforced(self):
        for expires in (False, True):
            with self.subTest(expires=expires):
                sid, session = self.login()
                session['checked'] = self.now[0] - 61
                entered, release = threading.Event(), threading.Event()

                def refresh(snapshot):
                    entered.set()
                    if not release.wait(3):
                        raise AssertionError('Refresh was not released')
                    return snapshot['identity']

                self.provider.refresh.reset_mock()
                self.provider.refresh.side_effect = refresh
                with ThreadPoolExecutor(max_workers=4) as workers:
                    pending = [workers.submit(self.auth.lookup, sid)]
                    try:
                        self.assertTrue(entered.wait(1))
                        pending.extend(workers.submit(self.auth.lookup, sid) for _ in range(3))
                        if expires:
                            self.now[0] += 301
                    finally:
                        release.set()
                    for future in pending:
                        self.assertIs(future.result(timeout=1), None if expires else session)
                self.provider.refresh.assert_called_once()

    def test_collegium_can_read_but_other_administration_routes_stay_blocked(self):
        sid, session = self.login()
        page = self.handler('/', sid=sid);page.do_GET()
        self.assertEqual(page._send.call_args.args[0], 200)
        self.assertIn(b'data-role="reader"', page._send.call_args.args[1])
        for path in ('/api/fresh/start', '/api/intake/read', '/api/intake/read-many', '/api/intake/setup-many', '/api/pilot/start',
                     '/api/backup/start', '/api/backup/confirm', '/api/appearance/select', '/api/appearance/upload', '/api/refresh',
                     '/api/maintenance/start', '/api/pilot/review', '/api/intake/accept',
                     '/api/software/catalog','/api/software/profile','/api/software/delete',
                     '/api/software/plan','/api/software/start','/api/software/review','/api/software/inventory','/api/software/teacher/check','/api/software/package/upload',
                     '/api/playbook-groups/save','/api/playbook-groups/start','/api/playbook-groups/delete'):
            handler = self.handler(path, 'POST', sid, session['csrf']);handler.do_POST()
            self.assertEqual(handler._send.call_args.args[0], 403, path)
            self.assertFalse(handler.server.provision.mock_calls)
            self.assertFalse(handler.server.dashboard.mock_calls)

    def test_bulk_intake_routes_enforce_csrf_and_accept_full_lab_payload(self):
        sid,session=self.login('admin')
        for path,body,method in (
            ('/api/intake/read-many',dict(hosts=['LAB-01A','LAB-01B'],ssh_password='ephemeral',
                image_confirmed=True,replacement_fingerprints={}), 'start_many'),
            ('/api/intake/setup-many',dict(targets=[dict(run_id=format(i,'032x'),lab_interface='enx001122334466') for i in range(15)],
                ssh_password='ephemeral',sudo_password='ephemeral',confirmed=True,student='student'),'setup_many')):
            handler=self.handler(path,'POST',sid,'wrong',body);handler.do_POST()
            self.assertEqual(handler._send.call_args.args[0],403)
            self.assertFalse(handler.server.provision.mock_calls)
            handler=self.handler(path,'POST',sid,session['csrf'],body)
            getattr(handler.server.provision.intake,method).return_value=dict(runs=[])
            handler.do_POST()
            self.assertEqual(handler._send.call_args.args[0],202)
            getattr(handler.server.provision.intake,method).assert_called_once_with(**body)
            invalid=self.handler(path,'POST',sid,session['csrf'],dict(body,command='shell'));invalid.do_POST()
            self.assertEqual(invalid._send.call_args.args[0],400)
            self.assertFalse(invalid.server.provision.mock_calls)

    def test_software_readers_can_search_but_admin_actions_require_csrf_and_exact_payload(self):
        sid,session=self.login()
        handler=self.handler('/api/software/search/1/network',sid=sid)
        handler.server.software=Mock();handler.server.software.search.return_value=dict(packages=[])
        handler.do_GET();self.assertEqual(handler._send.call_args.args[0],200)
        handler.server.software.search.assert_called_once_with('network',1)
        sid,session=self.login('admin')
        for csrf,body,status in [('wrong',{},403),(session['csrf'],{'command':'shell'},400),(session['csrf'],{},202)]:
            handler=self.handler('/api/software/catalog','POST',sid,csrf,body)
            handler.server.software=Mock();handler.server.software.refresh_catalog.return_value=True
            handler.do_POST();self.assertEqual(handler._send.call_args.args[0],status)
            self.assertEqual(handler.server.software.refresh_catalog.call_count,int(status==202))

    def test_teacher_software_check_requires_admin_csrf_and_free_operation_lock(self):
        sid,session=self.login('admin')
        for csrf,body,free,status in [('wrong',{},True,403),(session['csrf'],{'command':'shell'},True,400),
                                     (session['csrf'],{},False,409),(session['csrf'],{},True,200)]:
            handler=self.handler('/api/software/teacher/check','POST',sid,csrf,body)
            handler.server.software=Mock()
            handler.server.software.deployments.lock.acquire.return_value=free
            handler.server.software.teacher.verify.return_value=dict(ready=True)
            handler.do_POST();self.assertEqual(handler._send.call_args.args[0],status)
            self.assertEqual(handler.server.software.teacher.verify.call_count,int(status==200))
            self.assertEqual(handler.server.software.deployments.lock.release.call_count,int(status==200))

    def test_software_selected_run_is_authenticated_and_excludes_private_dependency_plans(self):
        identity='a'*32
        handler=self.handler('/api/software/run/'+identity);handler.server.software=Mock();handler.do_GET()
        self.assertEqual(handler._send.call_args.args[0],401)
        handler.server.software.record.assert_not_called()
        sid,_=self.login()
        handler=self.handler('/api/software/run/'+identity,sid=sid);handler.server.software=Mock()
        handler.server.software.record.return_value=dict(id=identity,kind='plan',status='succeeded',results={},plans={'private':'digest'})
        handler.do_GET();self.assertEqual(handler._send.call_args.args[0],200)
        self.assertNotIn('plans',json.loads(handler._send.call_args.args[1]))
        handler.server.software.record.assert_called_once_with(identity)

    def test_own_package_upload_requires_admin_csrf_bounded_stream_and_free_lock(self):
        sid,session=self.login('admin')
        for csrf,size,free,status in [(session['csrf'],7,True,201),('wrong',7,True,403),
                                      (session['csrf'],512*1024*1024+1,True,413),(session['csrf'],7,False,409)]:
            handler=self.handler('/api/software/package/upload','POST',sid,csrf)
            handler.headers.replace_header('Content-Type','application/octet-stream')
            handler.headers.replace_header('Content-Length',str(size));handler.headers['X-LAB-Package-Name']='lab-example.deb'
            handler.rfile=io.BytesIO(b'fixture');handler.server.software=Mock()
            handler.server.software.own.upload.return_value=dict(name='lab-example')
            handler.server.deployments=SimpleNamespace(lock=Mock())
            handler.server.deployments.lock.acquire.return_value=free
            handler.do_POST();self.assertEqual(handler._send.call_args.args[0],status)
            self.assertEqual(handler.server.software.own.upload.call_count,int(status==201))
            if status==201:handler.server.software.own.upload.assert_called_once_with(handler.rfile,7,'lab-example.deb')

    def test_manual_maintenance_multiple_targets_requires_admin_csrf_and_exact_payload(self):
        sid, session = self.login('admin')
        body = dict(hosts=['LAB-01A', 'LAB-03B'], confirmed=True)
        for csrf, payload, status in [('wrong', body, 403), (session['csrf'], dict(body, host='LAB-08A'), 404),
                                      (session['csrf'], body, 202)]:
            handler = self.handler('/api/maintenance/start', 'POST', sid, csrf, payload)
            handler.server.provision.maintenance_start.return_value = {'started': True}
            handler.do_POST()
            self.assertEqual(handler._send.call_args.args[0], status)
            if status == 202:
                handler.server.provision.maintenance_start.assert_called_once_with(**body)
            else:
                handler.server.provision.maintenance_start.assert_not_called()

    def test_classroom_is_authenticated_and_readable_for_collegium(self):
        handler=self.handler('/api/classroom');handler.server.classroom=Mock();handler.do_GET()
        self.assertEqual(handler._send.call_args.args[0],401);handler.server.classroom.snapshot.assert_not_called()
        sid,_=self.login()
        handler=self.handler('/api/classroom',sid=sid);handler.server.classroom=Mock()
        handler.server.classroom.snapshot.return_value=dict(clients={},refreshing=False)
        handler.do_GET();self.assertEqual(handler._send.call_args.args[0],200)
        handler.server.classroom.snapshot.assert_called_once()

    def test_room_selection_route_accepts_exact_payload_and_keeps_role_and_csrf_guards(self):
        from dashboard.room import ACTIONS
        for role in ('admin', 'reader'):
            sid, session = self.login(role)
            for action in ACTIONS:
                for selection in (dict(targets=['LAB-03A','LAB-04B','LAB-05A','LAB-05B']), dict(scope='pilot-a')):
                    payload = dict(action=action, confirmed=True, **selection)
                    handler = self.handler('/api/room/start', 'POST', sid, session['csrf'], payload)
                    handler.server.room = Mock(); handler.server.room.start.return_value = dict(id='a'*32)
                    handler.do_POST()
                    self.assertEqual(handler._send.call_args.args[0], 202)
                    handler.server.room.start.assert_called_once_with(**payload)
        handler = self.handler('/api/room/start', 'POST', sid, 'wrong-csrf', payload)
        handler.server.room = Mock(); handler.do_POST()
        self.assertEqual(handler._send.call_args.args[0], 403)
        handler.server.room.start.assert_not_called()

        handler = self.handler('/api/room/start', 'POST', body=payload)
        handler.server.room = Mock();handler.do_POST()
        self.assertEqual(handler._send.call_args.args[0],401)
        handler.server.room.start.assert_not_called()
        handler = self.handler('/api/room/start', 'POST', sid, session['csrf'], payload)
        handler.headers.replace_header('Origin', 'https://elsewhere.invalid')
        handler.server.room = Mock();handler.do_POST()
        self.assertEqual(handler._send.call_args.args[0], 403)
        handler.server.room.start.assert_not_called()
        handler = self.handler('/api/room/start', 'POST', sid, session['csrf'], dict(payload, command='shell'))
        handler.server.room = Mock();handler.do_POST()
        self.assertEqual(handler._send.call_args.args[0],404)
        handler.server.room.start.assert_not_called()

    def test_backup_targets_need_admin_and_csrf_but_no_repeated_key_confirmation(self):
        sid,session=self.login('admin')
        handler=self.handler('/api/backup/start','POST',sid,session['csrf'],dict(targets=['download']))
        handler.server.backups=Mock(profile='apps');handler.server.backups.start.return_value=dict(id='a'*32)
        handler.do_POST()
        self.assertEqual(handler._send.call_args.args[0],202)
        handler.server.backups.start.assert_called_once_with(targets=['download'])
        handler=self.handler('/api/backup/start','POST',sid,'wrong',dict(targets=['download']))
        handler.server.backups=Mock();handler.do_POST()
        self.assertEqual(handler._send.call_args.args[0],403);handler.server.backups.start.assert_not_called()
        for role in ('reader',None):
            sid,_=self.login(role) if role else (None,None)
            handler=self.handler('/api/backup/download/'+'a'*32,sid=sid)
            handler.server.backups=Mock();handler.do_GET()
            self.assertEqual(handler._send.call_args.args[0],403 if role else 401)
            handler.server.backups.download.assert_not_called()

    def test_backup_cleanup_is_admin_only_with_csrf_and_exact_preview_token(self):
        for role in ('reader',None):
            sid,session=self.login(role) if role else (None,None)
            for method in ('GET','POST'):
                handler=self.handler('/api/backup/cleanup',method,sid,session['csrf'] if session else None,dict(token='a'*64,confirmed=True))
                handler.server.backups=Mock();handler.do_GET() if method=='GET' else handler.do_POST()
                self.assertEqual(handler._send.call_args.args[0],403 if role else 401)
                handler.server.backups.cleanup.assert_not_called();handler.server.backups.cleanup_plan.assert_not_called()
        sid,session=self.login('admin')
        handler=self.handler('/api/backup/cleanup',sid=sid)
        handler.server.backups=Mock();handler.server.backups.cleanup_plan.return_value=dict(candidates=[],token='a'*64)
        handler.do_GET();self.assertEqual(handler._send.call_args.args[0],200)
        handler=self.handler('/api/backup/cleanup','POST',sid,session['csrf'],dict(token='a'*64,confirmed=True))
        handler.server.backups=Mock();handler.server.backups.cleanup.return_value=dict(id='b'*32,status='running')
        handler.do_POST();self.assertEqual(handler._send.call_args.args[0],202)
        handler.server.backups.cleanup.assert_called_once_with('a'*64,True)
        handler=self.handler('/api/backup/cleanup','POST',sid,'wrong',dict(token='a'*64,confirmed=True))
        handler.server.backups=Mock();handler.do_POST()
        self.assertEqual(handler._send.call_args.args[0],403);handler.server.backups.cleanup.assert_not_called()

    def test_named_denial_is_audited_but_never_creates_session(self):
        user = dict(account='student.test', name='Testschueler', role=None)
        self.provider.complete.side_effect = auth.Denied(user)
        tid, url = self.auth.begin(); state = parse_qs(urlsplit(url).query)['state'][0]
        with self.assertRaises(auth.Denied) as error:
            self.auth.complete(tid, urlencode(dict(state=state, code='fixture')))
        self.assertIn('student.test', str(error.exception))
        self.assertIn('Kollegium', str(error.exception))
        self.assertFalse(self.auth.sessions)
        row = self.auth.audit.records()[0]
        self.assertEqual((row['outcome'], row['account'], row['name']), ('denied', 'student.test', 'Testschueler'))

    def test_login_log_is_admin_only_and_presence_is_authenticated(self):
        sid, session = self.login()
        for path in ('/api/logins', '/api/logins/page/2', '/anmeldungen'):
            handler = self.handler(path, sid=sid); handler.do_GET()
            self.assertEqual(handler._send.call_args.args[0], 403)
        handler = self.handler('/api/presence')
        handler.server.presence = Mock();handler.do_GET()
        self.assertEqual(handler._send.call_args.args[0], 401)
        handler.server.presence.snapshot.assert_not_called()
        sid, session = self.login('admin')
        handler = self.handler('/api/logins', sid=sid);handler.do_GET()
        self.assertEqual(handler._send.call_args.args[0], 200)
        self.assertTrue(json.loads(handler._send.call_args.args[1])['records'])

    def test_admin_needs_csrf_and_cookie_reader_never_inherits_basic_admin(self):
        sid, session = self.login('admin')
        handler = self.handler('/api/refresh', 'POST', sid);handler.do_POST()
        self.assertEqual(handler._send.call_args.args[0], 403);handler.server.dashboard.refresh.assert_not_called()
        handler = self.handler('/api/refresh', 'POST', sid, session['csrf']);handler.do_POST()
        handler.server.dashboard.refresh.assert_called_once()
        sid, session = self.login()
        handler = self.handler('/api/refresh', 'POST', sid, session['csrf'])
        handler.headers['Authorization'] = 'Basic '+base64.b64encode(b'lab-admin:fixture-password').decode()
        handler.do_POST();self.assertEqual(handler._send.call_args.args[0], 403)

    def test_reader_cannot_download_root_installer_and_can_logout(self):
        sid, session = self.login()
        handler = self.handler('/api/teacher/setup', sid=sid);handler.do_GET()
        self.assertEqual(handler._send.call_args.args[0], 403)
        handler = self.handler('/logout', 'POST', sid, session['csrf']);handler.do_POST()
        self.assertEqual(handler._send.call_args.args[0], 200);self.assertIsNone(self.auth.lookup(sid))

    def test_disabled_basic_and_public_bearer_are_rejected_internal_service_still_works(self):
        self.config['local_login_enabled'] = False
        header = 'Basic '+base64.b64encode(b'lab-admin:fixture-password').decode()
        for value in (header, 'Bearer '+self.auth.automation_token):
            handler = self.handler('/api/session');handler.headers['Authorization'] = value;handler.do_GET()
            self.assertEqual(handler._send.call_args.args[0], 401)
        handler = self.handler('/api/session', local=True)
        handler.headers['Authorization'] = 'Bearer '+self.auth.automation_token;handler.do_GET()
        self.assertEqual(handler._send.call_args.args[0], 200)
        self.assertEqual(json.loads(handler._send.call_args.args[1])['source'], 'automation')

    def test_callback_query_is_accepted_only_for_callback_and_never_logged(self):
        handler = self.handler('/oidc/callback?code=fixture-private-code&state=fixture-state')
        self.assertTrue(handler._local_path());self.assertIn('code=',handler.query)
        handler = self.handler('/api/session?code=fixture-private-code');self.assertFalse(handler._local_path())
        handler = self.handler('/oidc/callback?code=fixture-private-code&state=fixture-state')
        with patch('sys.stderr', new_callable=io.StringIO) as output: handler.log_request(400)
        self.assertNotIn('fixture-private-code',output.getvalue());self.assertNotIn('state=',output.getvalue())

    def test_real_http_redirect_cookies_roles_and_logout(self):
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        server.auth = self.auth; server.base_path = '/cisco-lab';server.https_host = 'lab-app.example.org'
        server.password_path = self.password;server.backups = SimpleNamespace(profile='apps')
        server.dashboard = Mock();server.dashboard.refresh.return_value = True
        worker = threading.Thread(target=server.serve_forever, daemon=True);worker.start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        def request(path, *, session_cookie=None, method='GET', csrf=None):
            conn = http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=5)
            headers = {'Host':f'127.0.0.1:{server.server_port}', 'X-Forwarded-Proto':'https',
                       'X-Forwarded-Host':'lab-app.example.org'}
            if session_cookie: headers['Cookie'] = session_cookie
            if method == 'POST': headers.update({'Origin':'https://lab-app.example.org','Content-Type':'application/json'})
            if csrf: headers['X-CSRF-Token'] = csrf
            with patch('sys.stderr',new_callable=io.StringIO):
                conn.request(method,'/cisco-lab'+path,body=b'{}' if method=='POST' else None,headers=headers)
                response=conn.getresponse();body=response.read();status=response.status;out_headers=response.getheaders();conn.close()
            return status,out_headers,body
        def get_cookie(headers,name):
            for key,value in headers:
                if key.lower()=='set-cookie' and value.startswith(name+'='):
                    return name+'='+SimpleCookie(value)[name].value
            self.fail('Expected cookie was not set')
        status,headers,body=request('/')
        self.assertEqual(status,401);self.assertFalse(any(key.lower()=='www-authenticate' for key,_ in headers))
        self.assertIn(b'IServ Login',body)
        from dashboard.server import AUTH_STYLESHEET
        self.assertIn(AUTH_STYLESHEET.encode(),body)
        status,headers,css=request(AUTH_STYLESHEET)
        self.assertEqual(status,200)
        self.assertIn(b'.login-card',css)
        self.assertIn('max-age=3600',dict(headers)['Cache-Control'])
        for role in ('reader','admin'):
            user = info('lab.admin',OWNER) if role=='admin' else info()
            self.provider.complete.return_value=dict(identity=auth.identity(user,self.config),subject='fixture',
                access='fixture-access',expires=self.now[0]+300)
            status,headers,_=request('/oidc/login');self.assertEqual(status,302)
            tx_cookie=get_cookie(headers,auth.TRANSACTION_COOKIE)
            location=dict(headers)['Location'];state=parse_qs(urlsplit(location).query)['state'][0]
            status,headers,_=request('/oidc/callback?'+urlencode(dict(code='fixture-code',state=state)),session_cookie=tx_cookie)
            self.assertEqual(status,303)
            session_cookie=get_cookie(headers,auth.SESSION_COOKIE)
            self.assertTrue(any('HttpOnly' in value and 'Secure' in value and 'SameSite=Lax' in value
                                for key,value in headers if key.lower()=='set-cookie'))
            status,_,body=request('/',session_cookie=session_cookie);self.assertEqual(status,200)
            self.assertIn(('data-role="'+role+'"').encode(),body)
            import re
            csrf=re.search(rb'name="cisco-lab-csrf" content="([^"]+)"',body).group(1).decode()
            self.assertEqual(request('/api/refresh',method='POST',session_cookie=session_cookie)[0],403)
            status,_,_=request('/api/refresh',method='POST',session_cookie=session_cookie,csrf=csrf)
            self.assertEqual(status,403 if role=='reader' else 202)
            self.assertEqual(request('/logout',method='POST',session_cookie=session_cookie,csrf=csrf)[0],200)
            self.assertEqual(request('/api/session',session_cookie=session_cookie)[0],401)


if __name__ == '__main__': unittest.main()
