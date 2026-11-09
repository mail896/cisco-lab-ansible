"""IServ OIDC, server-side sessions and fixed LAB roles.

Authorization code flow with PKCE and nonce,
RS256 ID tokens, matching UserInfo subject and authoritative IServ groups.
No tokens, authorization codes or client secrets are written to logs.
"""
import base64
import hashlib
import hmac
from http.cookies import CookieError, SimpleCookie
import json
import math
from pathlib import Path
import re
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

from dashboard.admission import read_settings, write_private

ISSUER = 'https://school.example.org'
CALLBACK = 'https://lab-app.example.org/cisco-lab/oidc/callback'
SCOPES = 'openid profile email iserv:uuid iserv:groups'
SESSION_COOKIE = '__Secure-cisco-lab-session'
TRANSACTION_COOKIE = '__Secure-cisco-lab-login'
TOKEN = re.compile(r'[A-Za-z0-9_-]{43}\Z')
ACCOUNT = re.compile(r'[a-zA-Z0-9][a-zA-Z0-9._-]{0,79}\Z')


class AuthError(ValueError):
    def __init__(self, message='Die Anmeldedaten von IServ konnten nicht sicher bestätigt werden. Bitte die Anmeldung über „IServ Login“ neu starten. Bleibt der Fehler bestehen, die LAB-Administration informieren.', status=400):
        super().__init__(message)
        self.status = status


class Denied(AuthError):
    def __init__(self, user=None, message=None):
        self.user = user
        account = (user.get('name') or user['account']) + ' (' + user['account'] + ')' if user else 'dieses IServ-Konto'
        super().__init__(message or ('Kein Zugang für ' + account + '. CISCO-LAB ist nur für Mitglieder der Gruppe „Kollegium“ freigeschaltet. Bitte mit einem berechtigten Kollegiumskonto anmelden.'), 403)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise AuthError('IServ ist vorübergehend nicht erreichbar. Bitte später erneut versuchen.', 503)


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def same(left, right):
    return isinstance(left, str) and isinstance(right, str) and hmac.compare_digest(left.encode(), right.encode())


def cookie_value(header, name):
    if not header or len(header) > 8192 or len(re.findall(r'(?:^|;\s*)' + re.escape(name) + '=', header)) != 1:
        return None
    try:
        value = SimpleCookie(header).get(name)
        return value.value if value and TOKEN.fullmatch(value.value) else None
    except (ValueError, AttributeError, CookieError):
        return None


def cookie(name, value, age, base):
    path = base + ('/oidc/' if name == TRANSACTION_COOKIE else '/')
    return f'{name}={value}; Path={path}; Max-Age={age}; Secure; HttpOnly; SameSite=Lax'


def groups(value):
    if isinstance(value, list):
        return value
    if not isinstance(value, dict):
        return []
    if any(isinstance(value.get(key), str) for key in ('name', 'act', 'id')):
        return [value]
    for key in ('groups', 'items', 'data'):
        if isinstance(value.get(key), list):
            return value[key]
    return list(value.values()) if all(isinstance(row, dict) for row in value.values()) else []


def identity(info, settings):
    if not isinstance(info, dict):
        raise Denied()
    identifiers = [info[key] for key in ('iserv:uuid', 'uuid') if key in info]
    try:
        if not identifiers or not all(isinstance(value, str) and str(uuid.UUID(value)) == value.lower() for value in identifiers):
            raise ValueError
        user_uuid = identifiers[0].lower()
        if any(value.lower() != user_uuid for value in identifiers):
            raise ValueError
    except (ValueError, AttributeError):
        raise Denied() from None
    account = info.get('preferred_username')
    if not isinstance(account, str) or not ACCOUNT.fullmatch(account):
        raise Denied()
    account = account.lower()
    admin = account == settings['admin_account'] and user_uuid == settings['admin_uuid']
    staff = any(isinstance(group, dict) and any(
        isinstance(group.get(key), str) and group[key].casefold() == settings['reader_group'].casefold()
        for key in ('name', 'act', 'id')) for group in groups(info.get('iserv:groups')))
    name = info.get('name')
    name = name.strip() if isinstance(name, str) and len(name) <= 200 else account
    user = dict(account=account, name=name or account, uuid=user_uuid, role='admin' if admin else 'reader')
    if not admin and not staff:
        raise Denied(dict(account=account, name=name or account, role=None))
    return user


def configuration(path):
    value = read_settings(path)
    expected = {'version', 'issuer', 'redirect_uri', 'client_id', 'client_secret', 'admin_account',
                'admin_uuid', 'reader_group', 'local_login_enabled'}
    if (set(value) != expected or value['version'] != 1 or value['issuer'] != ISSUER
            or value['redirect_uri'] != CALLBACK or value['admin_account'] != 'lab.admin'
            or value['reader_group'] != 'Kollegium' or type(value['local_login_enabled']) is not bool
            or any(not isinstance(value[key], str) or not 1 <= len(value[key]) <= 256
                   or any(char in value[key] for char in '\r\n\0') for key in ('client_id', 'client_secret'))):
        raise ValueError('Private LAB-OIDC-Konfiguration ist unvollständig oder ungültig.')
    if str(uuid.UUID(value['admin_uuid'])) != value['admin_uuid']:
        raise ValueError('Stabile IServ-Admin-Kennung fehlt.')
    return value


class Provider:
    def __init__(self, settings, fetch=None, clock=time.time):
        self.settings = settings
        self.fetch = fetch or self.http_json
        self.clock = clock
        self.lock = threading.RLock()
        self.metadata_cache = None
        self.keys_cache = None

    @staticmethod
    def endpoint(value):
        if not isinstance(value, str):
            raise AuthError(status=503)
        url = urllib.parse.urlsplit(value)
        if (url.scheme != 'https' or url.netloc != 'school.example.org'
                or url.username or url.password or url.fragment or not url.path.startswith('/')):
            raise AuthError(status=503)
        return value

    def http_json(self, url, *, data=None, bearer=None):
        self.endpoint(url)
        headers = {'Accept': 'application/json', 'User-Agent': 'CISCO-LAB OIDC'}
        if data is not None:
            data = urllib.parse.urlencode(data).encode()
            headers['Content-Type'] = 'application/x-www-form-urlencoded'
        if bearer:
            headers['Authorization'] = 'Bearer ' + bearer
        request = urllib.request.Request(url, data=data, headers=headers)
        try:
            with urllib.request.build_opener(NoRedirect()).open(request, timeout=8) as response:
                body = response.read(1024 * 1024 + 1)
                if len(body) > 1024 * 1024:
                    raise ValueError
            result = json.loads(body)
            if not isinstance(result, dict):
                raise ValueError
            return result
        except (OSError, ValueError, urllib.error.URLError):
            raise AuthError('IServ ist vorübergehend nicht erreichbar oder hat die Anmeldung abgelehnt. Bitte erneut versuchen.', 503) from None

    def metadata(self):
        with self.lock:
            if self.metadata_cache and self.metadata_cache[0] > self.clock():
                return self.metadata_cache[1]
            data = self.fetch(ISSUER + '/.well-known/openid-configuration')
            algorithms = data.get('id_token_signing_alg_values_supported')
            if data.get('issuer') != ISSUER or not isinstance(algorithms, list) or 'RS256' not in algorithms:
                raise AuthError(status=503)
            for key in ('authorization_endpoint', 'token_endpoint', 'userinfo_endpoint', 'jwks_uri'):
                self.endpoint(data.get(key))
            self.metadata_cache = self.clock() + 3600, data
            return data

    def begin(self, transaction):
        data = self.metadata()
        challenge = base64.urlsafe_b64encode(hashlib.sha256(transaction['verifier'].encode()).digest()).decode().rstrip('=')
        query = dict(client_id=self.settings['client_id'], redirect_uri=CALLBACK, scope=SCOPES,
                     response_type='code', state=transaction['state'], nonce=transaction['nonce'],
                     code_challenge=challenge, code_challenge_method='S256')
        separator = '&' if '?' in data['authorization_endpoint'] else '?'
        return data['authorization_endpoint'] + separator + urllib.parse.urlencode(query)

    def key(self, token):
        import jwt
        header = jwt.get_unverified_header(token)
        if header.get('alg') != 'RS256' or header.get('crit') or not isinstance(header.get('kid'), str):
            raise AuthError()
        with self.lock:
            for attempt in range(2):
                if not self.keys_cache or self.keys_cache[0] <= self.clock() or attempt:
                    data = self.fetch(self.metadata()['jwks_uri'])
                    if not isinstance(data.get('keys'), list) or len(data['keys']) > 100:
                        raise AuthError()
                    self.keys_cache = self.clock() + 300, data['keys']
                matches = [key for key in self.keys_cache[1] if isinstance(key, dict)
                           and key.get('kid') == header['kid'] and key.get('kty') == 'RSA'
                           and key.get('use', 'sig') == 'sig' and key.get('alg', 'RS256') == 'RS256'
                           and 'verify' in key.get('key_ops', ['verify'])]
                if len(matches) == 1:
                    return jwt.algorithms.RSAAlgorithm.from_jwk(matches[0])
                if len(matches) > 1:
                    break
        raise AuthError()

    def complete(self, code, transaction):
        import jwt
        data = self.metadata()
        tokens = self.fetch(data['token_endpoint'], data=dict(
            grant_type='authorization_code', code=code, redirect_uri=CALLBACK,
            client_id=self.settings['client_id'], client_secret=self.settings['client_secret'],
            code_verifier=transaction['verifier']))
        token, access = tokens.get('id_token'), tokens.get('access_token')
        if (not isinstance(token, str) or not 1 <= len(token) <= 32768
                or not isinstance(access, str) or not 1 <= len(access) <= 16384
                or any(char in access for char in '\r\n\0')
                or not isinstance(tokens.get('token_type'), str) or tokens['token_type'].lower() != 'bearer'):
            raise AuthError()
        try:
            claims = jwt.decode(token, self.key(token), algorithms=['RS256'], issuer=ISSUER,
                                audience=self.settings['client_id'], leeway=30,
                                options={'require': ['iss', 'aud', 'exp', 'iat', 'sub', 'nonce']})
            if (not same(claims['nonce'], transaction['nonce']) or not isinstance(claims['sub'], str)
                    or not 1 <= len(claims['sub']) <= 255
                    or any(type(claims[key]) not in (int, float) or not math.isfinite(claims[key]) for key in ('exp', 'iat'))
                    or ('azp' in claims and claims['azp'] != self.settings['client_id'])
                    or (isinstance(claims['aud'], list) and len(claims['aud']) > 1 and claims.get('azp') != self.settings['client_id'])):
                raise AuthError()
            for key, value in (('at_hash', access), ('c_hash', code)):
                expected = base64.urlsafe_b64encode(hashlib.sha256(value.encode()).digest()[:16]).decode().rstrip('=')
                if key in claims and not same(claims[key], expected):
                    raise AuthError()
            info = self.fetch(data['userinfo_endpoint'], bearer=access)
            if not same(info.get('sub'), claims['sub']):
                raise AuthError()
            # An explicitly empty UserInfo group list overrides ID-token groups.
            for key in ('iserv:groups', 'iserv:uuid', 'preferred_username'):
                if key not in info and key in claims:
                    info[key] = claims[key]
            user = identity(info, self.settings)
            seconds = float(tokens.get('expires_in', 300))
            expires = min(float(claims['exp']), self.clock() + seconds, self.clock() + 3600)
            if not math.isfinite(expires) or expires <= self.clock():
                raise AuthError()
            return dict(identity=user, subject=claims['sub'], access=access, expires=expires)
        except (jwt.PyJWTError, ValueError, TypeError, KeyError, OverflowError) as error:
            if isinstance(error, AuthError):
                raise
            raise AuthError() from None

    def refresh(self, session):
        info = self.fetch(self.metadata()['userinfo_endpoint'], bearer=session['access'])
        if not same(info.get('sub'), session['subject']):
            raise Denied()
        return identity(info, self.settings)


class Auth:
    def __init__(self, directory, settings, provider=None, clock=time.time):
        self.directory, self.settings, self.clock = directory, settings, clock
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.provider = provider or Provider(settings, clock=clock)
        self.lock = threading.RLock()
        self.slots = threading.BoundedSemaphore(4)
        self.transactions, self.sessions = {}, {}
        self.login_times = []
        path = directory / 'automation-token.json'
        if not path.exists():
            write_private(path, json.dumps({'token': secrets.token_urlsafe(32)}))
        self.automation_token = read_settings_token(path)
        from dashboard.login_audit import LoginAudit
        self.audit = LoginAudit(directory / 'logins', clock=clock)

    @property
    def local_login_enabled(self):
        return self.settings['local_login_enabled']

    def purge(self):
        for mapping in (self.transactions, self.sessions):
            for key in list(mapping):
                if mapping[key]['expires'] <= self.clock():
                    del mapping[key]

    def begin(self, previous=None):
        with self.lock:
            self.purge()
            self.login_times = [stamp for stamp in self.login_times if stamp > self.clock() - 60]
            if len(self.login_times) >= 30 or len(self.transactions) >= 100:
                raise AuthError('Zu viele Anmeldeversuche. Bitte in einer Minute erneut versuchen.', 429)
            self.login_times.append(self.clock())
            if previous:
                self.transactions.pop(digest(previous), None)
        if not self.slots.acquire(blocking=False):
            raise AuthError('IServ-Anmeldung ist ausgelastet. Bitte gleich erneut versuchen.', 503)
        try:
            tid = secrets.token_urlsafe(32)
            tx = dict(state=secrets.token_urlsafe(32), nonce=secrets.token_urlsafe(32),
                      verifier=secrets.token_urlsafe(48), expires=self.clock() + 300)
            url = self.provider.begin(tx)
            with self.lock:
                self.transactions[digest(tid)] = tx
            return tid, url
        finally:
            self.slots.release()

    def complete(self, tid, query, previous_sid=None):
        with self.lock:
            self.purge()
            tx = self.transactions.pop(digest(tid), None) if tid else None
        values = urllib.parse.parse_qs(query, keep_blank_values=True, max_num_fields=10)
        if (not tx or any(len(value) != 1 for value in values.values())
                or not same(values.get('state', [''])[0], tx['state'])
                or ('iss' in values and values['iss'][0] != ISSUER)):
            raise AuthError('Die Anmeldung ist abgelaufen oder ungültig. Bitte neu beginnen.')
        if values.get('error'):
            if values['error'][0] == 'access_denied':
                self.audit.append('denied', 'provider_denied')
                raise Denied(message='IServ hat den Zugang nicht freigegeben oder die Anmeldung wurde abgebrochen. CISCO-LAB ist für Mitglieder des Kollegiums vorgesehen. IServ hat dabei keinen Kontonamen an die LAB-Verwaltung übermittelt.')
            self.audit.append('failed', 'verification_failed')
            raise AuthError()
        code = values.get('code', [''])[0]
        if not 1 <= len(code) <= 2048 or any(char in code for char in '\r\n\0'):
            raise AuthError()
        if not self.slots.acquire(blocking=False):
            raise AuthError('IServ-Anmeldung ist ausgelastet. Bitte neu beginnen.', 503)
        try:
            session = self.provider.complete(code, tx)
        except Denied as error:
            self.audit.append('denied', 'not_collegium' if error.user else 'verification_failed', error.user)
            raise
        except AuthError as error:
            self.audit.append('failed', 'provider_unavailable' if error.status == 503 else 'verification_failed')
            raise
        finally:
            self.slots.release()
        with self.lock:
            self.purge()
            if len(self.sessions) >= 1000:
                raise AuthError('Anmeldung ist ausgelastet. Bitte später erneut versuchen.', 503)
            self.audit.append('success', 'signed_in', session['identity'])
            if previous_sid:
                self.sessions.pop(digest(previous_sid), None)
            sid = secrets.token_urlsafe(32)
            session.update(csrf=secrets.token_urlsafe(32), checked=self.clock())
            self.sessions[digest(sid)] = session
            if session['identity']['role'] == 'admin':
                proof = dict(account=session['identity']['account'], uuid=session['identity']['uuid'],
                             client_id=self.settings['client_id'], verified_at=self.clock())
                target = self.directory / 'admin-verified.json'
                tmp = target.with_suffix('.tmp'); tmp.unlink(missing_ok=True)
                write_private(tmp, json.dumps(proof)); tmp.replace(target)
            return sid, session

    def lookup(self, sid):
        key = digest(sid) if sid else None
        with self.lock:
            self.purge()
            session = self.sessions.get(key)
            if not session:
                return None
            if session['checked'] >= self.clock() - 60:
                return session
            refresh_lock = session.setdefault('_refresh_lock', threading.Lock())
        # Only requests for this session wait for its IServ check. Other sessions
        # and logout stay responsive; concurrent requests share one fresh check.
        with refresh_lock:
            with self.lock:
                self.purge()
                if self.sessions.get(key) is not session:
                    return None
                if session['checked'] >= self.clock() - 60:
                    return session
                snapshot = dict(session)
            try:
                identity = self.provider.refresh(snapshot)
            except Denied:
                with self.lock:
                    if self.sessions.get(key) is session:
                        self.sessions.pop(key, None)
                raise
            with self.lock:
                self.purge()
                if self.sessions.get(key) is not session:
                    return None
                session.update(identity=identity, checked=self.clock())
                return session

    def logout(self, sid):
        with self.lock:
            if sid:
                self.sessions.pop(digest(sid), None)


def read_settings_token(path):
    data = read_settings(path)
    value = data.get('token')
    if set(data) != {'token'} or not isinstance(value, str) or not TOKEN.fullmatch(value):
        raise ValueError('Interner LAB-Dienstzugang ist ungültig.')
    return value
