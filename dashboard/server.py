"""Local LAB maintenance dashboard and single-client pilot deployments."""
import argparse
import base64
import binascii
import fcntl
import hashlib
import hmac
import html
import os
import re
import secrets
import stat
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import subprocess
import sys
import urllib.parse

from dashboard.appearance import Appearance, MAX_FILE as MAX_IMAGE_UPLOAD
from dashboard.activity import Activity
from dashboard.auth import (Auth, AuthError, SESSION_COOKIE, TRANSACTION_COOKIE,
                            configuration as oidc_configuration, cookie, cookie_value, same)
from dashboard.appearance_release import AppearanceRelease
from dashboard.backup import Backups
from dashboard.deploy import Deployments, catalog
from dashboard.provision import ENTRIES, Provision, source
from dashboard.presence import Presence
from dashboard.pagination import PAGE_SIZE, paginate
from dashboard.runtime import ansible_tool
from dashboard.room import ACTIONS, CAPABILITIES, SCOPES, RoomActions
from dashboard.software import Software
from dashboard.playbook_groups import PlaybookGroups
from dashboard.classroom import Classroom
from dashboard.targets import HOSTS
from dashboard.client_report import Dashboard

ROOT = Path(__file__).resolve().parents[1]
ASSETS = {
    '/': ('index.html', 'text/html; charset=utf-8'),
    '/app.css': ('app.css', 'text/css; charset=utf-8'),
    '/app.js': ('app.js', 'text/javascript; charset=utf-8'),
    '/history.js': ('history.js', 'text/javascript; charset=utf-8'),
    '/room_layout.js': ('room_layout.js', 'text/javascript; charset=utf-8'),
    '/room_layout.css': ('room_layout.css', 'text/css; charset=utf-8'),
    '/catalog.js': ('catalog.js', 'text/javascript; charset=utf-8'),
    '/intake.js': ('intake.js', 'text/javascript; charset=utf-8'),
    '/software.js': ('software.js', 'text/javascript; charset=utf-8'),
    '/playbook_groups.js': ('playbook_groups.js', 'text/javascript; charset=utf-8'),
    '/classroom.js': ('classroom.js', 'text/javascript; charset=utf-8'),
}
PUBLIC_ICONS = {
    '/assets/cisco-lab.svg': ('assets/cisco-lab.svg', 'image/svg+xml'),
    '/assets/cisco-lab-icon.png': ('assets/cisco-lab-icon.png', 'image/png'),
}
AUTH_STYLESHEET = '/auth.' + hashlib.sha256((ROOT / 'dashboard/auth.css').read_bytes()).hexdigest()[:12] + '.css'
PUBLIC_AUTH_ASSETS = {path: ('auth.css', 'text/css; charset=utf-8') for path in ('/auth.css', AUTH_STYLESHEET)}
THEME_SCRIPT = '/theme.' + hashlib.sha256((ROOT/'dashboard/theme.js').read_bytes()).hexdigest()[:12] + '.js'
THEME_STYLESHEET = '/theme.' + hashlib.sha256((ROOT/'dashboard/theme.css').read_bytes()).hexdigest()[:12] + '.css'
PUBLIC_THEME_ASSETS = {THEME_SCRIPT:('theme.js','text/javascript; charset=utf-8'),
                       THEME_STYLESHEET:('theme.css','text/css; charset=utf-8')}
def icon(paths, *, fill=False):
    style = 'fill="currentColor"' if fill else 'fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"'
    return '<svg width="20" height="20" viewBox="0 0 24 24" aria-hidden="true" focusable="false" ' + style + '>' + paths + '</svg>'


MAIL_ICON = icon('<rect x="3" y="5" width="18" height="14" rx="2"/><path d="m3 7 9 6 9-6"/>')
GITHUB_ICON = icon('<path d="M12 .7a11.5 11.5 0 0 0-3.64 22.41c.58.11.79-.25.79-.55v-2.14c-3.23.7-3.91-1.37-3.91-1.37-.53-1.34-1.29-1.7-1.29-1.7-1.05-.72.08-.7.08-.7 1.16.08 1.77 1.19 1.77 1.19 1.03 1.77 2.69 1.26 3.35.96.1-.75.4-1.26.73-1.55-2.58-.29-5.29-1.29-5.29-5.75 0-1.27.45-2.3 1.19-3.11-.12-.29-.52-1.47.11-3.07 0 0 .97-.31 3.16 1.19a10.9 10.9 0 0 1 5.76 0c2.2-1.5 3.16-1.19 3.16-1.19.63 1.6.23 2.78.11 3.07.74.81 1.19 1.84 1.19 3.11 0 4.47-2.72 5.45-5.3 5.74.42.36.79 1.06.79 2.14v3.18c0 .3.21.67.8.55A11.5 11.5 0 0 0 12 .7Z"/>', fill=True)
PAGES_ICON = icon('<path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9Z"/><path d="M14 3v6h6M8 13h8M8 17h6"/>')
LOGIN_ICON = icon('<path d="M14 4h5a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-5M3 12h12m-4-4 4 4-4 4"/>')
THEME_PICKER = ('<label class="theme-picker">'+icon('<path d="M20 13.1A8 8 0 0 1 10.9 4 8 8 0 1 0 20 13.1Z"/>')+
                '<span class="theme-label">Darstellung</span><select data-theme-select aria-label="Darstellung">'
                '<option value="system">System</option><option value="light">Hell</option>'
                '<option value="dark">Dunkel</option></select></label>')
APP_VERSION = '2026.11.09'
FOOTER = ('<footer class="app-footer"><span class="footer-credit">2026 - Marc Schulz'
          '<a class="mail-icon" href="mailto:marc.schulz@bbs-einbeck.de" aria-label="E-Mail an Marc Schulz" title="E-Mail an Marc Schulz">' + MAIL_ICON + '</a></span>'
          '<a href="https://github.com/mail896/cisco-lab-ansible" target="_blank" rel="noopener noreferrer">' + GITHUB_ICON + 'GitHub</a>'
          '<a href="https://mail896.github.io/cisco-lab-ansible/" target="_blank" rel="noopener noreferrer">' + PAGES_ICON + 'Pages</a>'
          '<span class="app-version">Version ' + APP_VERSION + '</span></footer>')
for page in ('/schuelerclients', '/installationsfolge', '/pilotdeploys',
             '/sammelaktionen', '/backup', '/desktopbilder', '/playbooks', '/wartung', '/anmeldungen', '/repo-software','/unterricht'):
    ASSETS[page] = ASSETS['/']


def teacher_installer():
    return teacher_script('teacher-installer.py')


def teacher_script(name):
    if name not in ('teacher-installer.py', 'retire_legacy.py'):
        raise ValueError('Unbekanntes Teacher-Paket.')
    path = ROOT / 'deployment/teacher' / name
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022
            or info.st_size > 200000):
        raise ValueError('Teacher-Installationspaket ist nicht rootgeschützt.')
    return path.read_bytes()


def deployment_plan(host):
    if host not in HOSTS:
        raise ValueError('Bitte einen LAB-Client auswählen.')
    args = [ansible_tool('ansible-playbook'), '-i', 'inventory.ini', '--limit', host, '--list-tasks', 'lab-baseline.yml']
    result = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, timeout=15, check=True)
    tasks = [line.split('\tTAGS:', 1)[0].strip()
             for line in result.stdout.splitlines() if '\tTAGS:' in line]
    return dict(host=host, tasks=tasks)


def read_credential(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as file:
        info = os.fstat(file.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError('Passwortdatei muss privat sein.')
        password = file.read(1025).rstrip(b'\r\n')
    if not 0 < len(password) <= 1024 or b'\r' in password or b'\n' in password:
        raise ValueError('Passwortdatei muss genau ein Passwort enthalten.')
    return b'lab-admin:' + password


class Handler(BaseHTTPRequestHandler):
    # Bounds stalled request bodies and uploads, not the total transfer duration.
    timeout = 30

    def _local_path(self):
        """Accept only this application's configured URL prefix."""
        base = self.server.base_path
        path = self.path
        self.query = ''
        if '#' in path or not path.startswith('/') or len(path) > 8192:
            return False
        if '?' in path:
            path, self.query = path.split('?', 1)
            if path != base + '/oidc/callback':
                return False
        if base:
            if path == base:
                self.path = '/'
            elif path.startswith(base + '/'):
                self.path = path[len(base):]
            else:
                return False
        else:
            self.path = path
        return True

    def _request_origin(self):
        port = self.server.server_port
        host = self.headers.get('Host')
        proto = self.headers.get('X-Forwarded-Proto')
        if proto is None and host in {f'127.0.0.1:{port}', f'localhost:{port}'}:
            return f'http://{host}'
        if proto == 'https' and self.server.https_host:
            if host == self.server.https_host:
                return f'https://{host}'
            forwarded = self.headers.get_all('X-Forwarded-Host', [])
            if (self.client_address[0] == '127.0.0.1'
                    and host in {f'127.0.0.1:{port}', f'localhost:{port}'}
                    and forwarded == [self.server.https_host]):
                return f'https://{self.server.https_host}'
        return None

    def _trusted_request(self, require_origin=False):
        expected = self._request_origin()
        origin = self.headers.get('Origin')
        return expected is not None and (origin == expected or (origin is None and not require_origin))

    def _authorized(self):
        auth = getattr(self.server, 'auth', None)
        self.session = None
        if auth:
            raw_cookie = self.headers.get('Cookie', '')
            sid = cookie_value(raw_cookie, SESSION_COOKIE)
            if SESSION_COOKIE + '=' in raw_cookie:
                try:
                    session = auth.lookup(sid)
                except AuthError as error:
                    self._auth_error(error)
                    return False
                if session:
                    self.session = session
                    self.identity = dict(session['identity'], source='oidc')
                    return True
                return self._unauthorized()
            bearer = self.headers.get('Authorization', '')
            if (self.client_address[0] == '127.0.0.1' and self._request_origin().startswith('http://')
                    and not any(self.headers.get(key) for key in ('X-Forwarded-Proto', 'X-Forwarded-Host', 'Forwarded'))
                    and same(bearer, 'Bearer ' + auth.automation_token)):
                self.identity = dict(role='admin', account='LAB-Dienst', name='LAB-Dienst', source='automation')
                return True
            if not auth.local_login_enabled:
                return self._unauthorized()
        try:
            scheme, token = self.headers.get('Authorization', '').split(' ', 1)
            supplied = base64.b64decode(token, validate=True)
            allowed = scheme.lower() == 'basic' and hmac.compare_digest(
                supplied, read_credential(self.server.password_path)
            )
        except (OSError, ValueError, binascii.Error):
            allowed = False
        if not allowed:
            return self._unauthorized()
        self.identity = dict(role='admin', account='lab-admin', name='Lokaler Admin', source='local')
        return allowed

    def _unauthorized(self):
        auth = getattr(self.server, 'auth', None)
        if auth and not getattr(self, 'basic_challenge', False):
            if self.path.startswith('/api/') or self.command == 'POST':
                self._json(401, {'error': 'Bitte mit IServ anmelden.', 'login_url': self.server.base_path + '/oidc/login'})
            else:
                self._login_page(status=401)
        else:
            self._send(401, b'Anmeldung erforderlich.', 'text/plain; charset=utf-8')
        return False

    def _login_page(self, message='', status=200):
        base = self.server.base_path
        auth = self.server.auth
        notice = '<p class="notice" role="status">' + html.escape(message) + '</p>' if message else ''
        local = ('<details><summary>Lokaler Zugang während der Umstellung</summary><p><a href="' + base +
                 '/local">Mit lokalem LAB-Zugang anmelden</a></p></details>') if auth.local_login_enabled else ''
        body = ('<!doctype html><html lang="de"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
                '<title>Anmeldung · CISCO-LAB</title><link rel="icon" type="image/png" href="' + base + '/assets/cisco-lab-icon.png">'
                '<script src="'+base+THEME_SCRIPT+'"></script>'
                '<link rel="stylesheet" href="' + base + AUTH_STYLESHEET + '">'
                '<link rel="stylesheet" href="'+base+THEME_STYLESHEET+'"></head><body><div class="landing"><header class="brand">'
                '<img src="' + base + '/assets/cisco-lab.svg" width="48" height="48" alt="">'
                '<span>CISCO-LAB<small>BBS Einbeck</small></span>'+THEME_PICKER+'</header><main>'
                '<section class="intro" aria-labelledby="welcome-title"><p class="eyebrow">Das Netzwerklabor im Blick</p>'
                '<h1 id="welcome-title">Ein Raum.<br>Alles im Blick.</h1>'
                '<p class="lead">Die zentrale Übersicht für unsere LAB-Rechner: Gerätestand, Installationen und Wartung an einem Ort.</p>'
                '<div class="access-notes"><div><span class="access-dot"></span><h2>Für das Kollegium</h2>'
                '<p>LAB-Status ansehen und Sammelaktionen ausführen.</p></div>'
                '<div><span class="access-dot admin-dot"></span><h2>Für die Administration</h2>'
                '<p>Geräte einrichten, Sammelaktionen starten und das LAB verwalten.</p></div></div></section>'
                '<section class="login-card" aria-labelledby="login-title"><span class="login-mark">' + LOGIN_ICON + '</span>'
                '<p class="eyebrow">Geschützter Schulbereich</p><h2 id="login-title">Willkommen im LAB</h2>'
                '<p>Melde dich mit deinem IServ-Konto der BBS Einbeck an.</p>' + notice +
                '<a class="button" href="' + base + '/oidc/login">IServ Login' + LOGIN_ICON + '</a>'
                '<p class="login-hint">Die Anmeldung erfolgt direkt bei IServ. Zugriff haben nur Mitglieder des Kollegiums. '
                'Andere Konten erhalten keinen Zugang.</p>' + local + '</section></main>' + FOOTER + '</div></body></html>')
        self._send(status, body.encode(), 'text/html; charset=utf-8')

    def _auth_error(self, error, *, clear_transaction=False):
        headers = []
        if clear_transaction:
            headers.append(('Set-Cookie', cookie(TRANSACTION_COOKIE, '', 0, self.server.base_path)))
        if self.path.startswith('/api/') or self.command == 'POST':
            return self._send(error.status, json.dumps({'error': str(error)}, ensure_ascii=False).encode(),
                              'application/json; charset=utf-8', headers=headers)
        # All messages are fixed local strings, never OAuth error descriptions.
        if headers:
            self.extra_auth_headers = headers
        return self._login_page(str(error), status=error.status)

    def _auth_route(self):
        auth = getattr(self.server, 'auth', None)
        if not auth:
            return False
        base = self.server.base_path
        if self.path in ('/oidc/login', '/oidc/callback', '/local') and self.command != 'GET':
            self._json(405, {'error': 'Diese Anmelderoute erfordert GET.'})
            return True
        if self.path == '/oidc/login':
            try:
                tid, url = auth.begin(cookie_value(self.headers.get('Cookie', ''), TRANSACTION_COOKIE))
                self._send(302, b'', 'text/plain; charset=utf-8', headers=[('Location', url),
                           ('Set-Cookie', cookie(TRANSACTION_COOKIE, tid, 300, base))])
            except AuthError as error:
                self._auth_error(error)
            except (ValueError, TypeError, OSError):
                self._auth_error(AuthError('IServ ist vorübergehend nicht erreichbar. Bitte erneut versuchen.', 503))
            return True
        if self.path == '/oidc/callback':
            try:
                raw = self.headers.get('Cookie', '')
                sid, session = auth.complete(cookie_value(raw, TRANSACTION_COOKIE), self.query,
                                             cookie_value(raw, SESSION_COOKIE))
                age = max(1, int(session['expires'] - auth.clock()))
                self._send(303, b'', 'text/plain; charset=utf-8', headers=[('Location', base + '/'),
                           ('Set-Cookie', cookie(TRANSACTION_COOKIE, '', 0, base)),
                           ('Set-Cookie', cookie(SESSION_COOKIE, sid, age, base))])
            except AuthError as error:
                self._auth_error(error, clear_transaction=True)
            except (ValueError, TypeError, OSError):
                self._auth_error(AuthError(), clear_transaction=True)
            return True
        if self.path == '/local':
            if not auth.local_login_enabled:
                self._json(404, {'error': 'Der lokale Browserzugang ist abgeschaltet.'})
            else:
                self.basic_challenge = True
                if self._authorized():
                    self._send(303, b'', 'text/plain; charset=utf-8', headers=[('Location', base + '/')])
            return True
        return False

    def _admin(self):
        # Legacy standalone installations retain their existing single admin.
        if getattr(self, 'identity', {'role': 'admin'})['role'] != 'admin':
            self._json(403, {'error': 'Du hast Lesezugriff mit Sammelaktionen. Diese Verwaltungsaktion darf nur der Administrator starten.'})
            return False
        return True

    def _csrf(self):
        if getattr(self, 'session', None) and not same(self.headers.get('X-CSRF-Token'), self.session['csrf']):
            self._json(403, {'error': 'Die Sicherheitsprüfung ist abgelaufen. Bitte die Seite neu laden.'})
            return False
        return True

    def _send(self, status, body, kind, *, cache_control='no-store', headers=()):
        self._headers(status, kind, len(body), cache_control=cache_control, headers=headers)
        if self.command != 'HEAD':
            self.wfile.write(body)

    def _headers(self, status, kind, length=None, *, cache_control='no-store', headers=()):
        self.send_response(status)
        if status == 401 and (not getattr(self.server, 'auth', None) or getattr(self, 'basic_challenge', False)):
            self.send_header('WWW-Authenticate', 'Basic realm="CISCO-LAB lokal", charset="UTF-8"')
        self.send_header('Content-Type', kind)
        if length is not None:
            self.send_header('Content-Length', str(length))
        self.send_header('Cache-Control', cache_control)
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; object-src 'none'; frame-ancestors 'none'")
        for name, value in [*headers, *getattr(self, 'extra_auth_headers', [])]:
            self.send_header(name, value)
        self.end_headers()

    def _json(self, status, data):
        self._send(status, json.dumps(data, ensure_ascii=False).encode(), 'application/json; charset=utf-8')

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        if not self._local_path():
            return self._json(404, {'error': 'Seite nicht gefunden.'})
        if not self._trusted_request():
            return self._json(403, {'error': 'Nur lokaler Zugriff.'})
        self.history_page = None
        paged = re.fullmatch(r'(/api/(?:playbook-groups|software|pilot|room|backup|fresh|logins|maintenance-runs|teacher/runs|appearance/rollout|rollout/state/[a-z-]+))/page/([1-9][0-9]{0,5})', self.path)
        if paged:
            self.path, number = paged.groups()
            self.history_page = int(number)
        if self.path in PUBLIC_ICONS:
            filename, kind = PUBLIC_ICONS[self.path]
            return self._send(200, (Path(__file__).parent / filename).read_bytes(), kind,
                              cache_control='public, max-age=86400')
        if self.path in PUBLIC_AUTH_ASSETS:
            filename, kind = PUBLIC_AUTH_ASSETS[self.path]
            return self._send(200, (Path(__file__).parent / filename).read_bytes(), kind,
                              cache_control='public, max-age=3600')
        if self.path in PUBLIC_THEME_ASSETS:
            filename,kind=PUBLIC_THEME_ASSETS[self.path]
            return self._send(200,(Path(__file__).parent/filename).read_bytes(),kind,
                              cache_control='public, max-age=86400')
        if self._auth_route():
            return
        if not self._authorized():
            return
        if self.path == '/api/session':
            user = self.identity
            return self._json(200, {key: user[key] for key in ('role', 'account', 'name', 'source')})
        if self.path == '/api/classroom':
            return self._json(200,self.server.classroom.snapshot())
        if self.path == '/api/playbook-groups' or self.path.startswith('/api/playbook-groups/'):
            try:
                if self.path == '/api/playbook-groups':
                    return self._json(200, paginate(self.server.playbook_groups.state(), self.history_page))
                match = re.fullmatch(r'/api/playbook-groups/log/([a-f0-9]{32})/(LAB-0[1-8][AB])', self.path)
                if match:
                    return self._json(200, dict(output=self.server.playbook_groups.output(match[1], match[2])))
                return self._json(404, dict(error='Playbook-Gruppe nicht gefunden.'))
            except FileNotFoundError:
                return self._json(404, dict(error='Gruppe oder Protokoll nicht gefunden.'))
            except ValueError as error:
                return self._json(400, dict(error=str(error)))
            except OSError:
                return self._json(503, dict(error='Playbook-Gruppen konnten nicht gelesen werden.'))
        if self.path == '/api/software' or self.path.startswith('/api/software/'):
            try:
                if self.path == '/api/software':
                    return self._json(200, paginate(self.server.software.state(), self.history_page))
                match = re.fullmatch(r'/api/software/run/([a-f0-9]{32})',self.path)
                if match:
                    row=self.server.software.record(match[1])
                    return self._json(200,{key:value for key,value in row.items() if key!='plans'})
                match = re.fullmatch(r'/api/software/search/([1-9][0-9]{0,5})/(.*)',self.path)
                if match:
                    return self._json(200,self.server.software.search(urllib.parse.unquote(match[2]),int(match[1])))
                match = re.fullmatch(r'/api/software/export/([a-f0-9]{32})',self.path)
                if match:
                    software=self.server.software
                    if software.own.snapshot(software.profile(match[1])['packages']):
                        self._headers(200,'application/zip',None,headers=[('Content-Disposition','attachment; filename="lab-softwareprofil.zip"')])
                        return software.write_export_zip(match[1],self.wfile)
                    return self._send(200,self.server.software.export(match[1]),'application/yaml; charset=utf-8',
                                      headers=[('Content-Disposition','attachment; filename="lab-softwareprofil.yml"')])
                match = re.fullmatch(r'/api/software/log/([a-f0-9]{32})/(LAB-(?:0[1-8][AB]|Teacher))',self.path)
                if match:
                    return self._json(200,dict(output=self.server.software.output(match[1],match[2])))
                return self._json(404,dict(error='Softwareansicht nicht gefunden.'))
            except FileNotFoundError:
                return self._json(404,dict(error='Softwareprofil oder Protokoll nicht gefunden.'))
            except ValueError as error:
                return self._json(400,dict(error=str(error)))
            except (OSError,sqlite3.Error):
                return self._json(503,dict(error='Softwaredaten konnten nicht gelesen werden.'))
        if self.path in ('/api/logins', '/anmeldungen'):
            if not self._admin():
                return
            if self.path == '/api/logins':
                auth = getattr(self.server, 'auth', None)
                if not auth:
                    return self._json(404, {'error': 'IServ-Anmeldeprotokoll ist hier nicht eingerichtet.'})
                try:
                    records = auth.audit.iter_records() if self.history_page else auth.audit.records()
                    return self._json(200, paginate({'records': records, 'retention_days': 90,
                                                     'limit': PAGE_SIZE if self.history_page else 200}, self.history_page, 'records'))
                except (OSError, ValueError):
                    return self._json(503, {'error': 'Anmeldeprotokoll konnte nicht sicher gelesen werden.'})
        if self.path == '/api/presence':
            return self._json(200, self.server.presence.snapshot())
        if self.path == '/api/activity':
            try:
                return self._json(200, self.server.activity.snapshot())
            except (OSError, ValueError):
                return self._json(503, {'error': 'Auftragsstatus derzeit nicht verfügbar.'})
        if self.path == '/api/teacher/runs' and self.history_page:
            if self.server.backups.profile != 'apps':
                return self._json(404, {'error': 'Teacher-Verlauf ist hier nicht eingerichtet.'})
            from maintenance import teacher_controller
            try:
                return self._json(200, paginate({'runs': teacher_controller.history()}, self.history_page))
            except (OSError, ValueError):
                return self._json(503, {'error': 'Teacher-Verlauf konnte nicht gelesen werden.'})
        if self.path in ('/api/teacher/setup', '/api/teacher/cleanup', '/api/teacher/status'):
            if not self.path.endswith('/status') and not self._admin():
                return
            if self.server.backups.profile != 'apps':
                return self._json(404, {'error': 'Teacher-Einrichtung nur auf APP.'})
            try:
                if self.path.endswith('/setup'):
                    return self._send(200, teacher_installer(), 'text/x-python; charset=utf-8',
                                      headers=[('Content-Disposition', 'attachment; filename="cisco-lab-teacher-setup.py"')])
                if self.path.endswith('/cleanup'):
                    return self._send(200, teacher_script('retire_legacy.py'), 'text/x-python; charset=utf-8',
                                      headers=[('Content-Disposition', 'attachment; filename="cisco-lab-teacher-cleanup.py"')])
                from maintenance import teacher_controller
                return self._json(200, {'configuration': teacher_controller.configuration(),
                                        'runs': teacher_controller.history()[:10]})
            except (OSError, ValueError):
                return self._json(503, {'error': 'Teacher-Vorbereitung noch nicht vollständig verfügbar.'})
        if self.path == '/api/playbooks':
            return self._json(200, ENTRIES)
        if self.path.startswith('/api/playbooks/'):
            entry = self.path.rsplit('/',1)[-1]
            try:
                content = source(entry)
            except FileNotFoundError:
                return self._json(404, {'error': 'Playbook nicht gefunden.'})
            except (OSError, UnicodeError):
                return self._json(503, {'error': 'Playbook-Quelltext konnte nicht gelesen werden. Bitte erneut versuchen.'})
            return self._json(200, {'content': content, 'entry': ENTRIES[entry]})
        if self.path.startswith('/api/rollout/state/'):
            job = self.path.rsplit('/',1)[-1]
            try:
                data = self.server.appearance_release.state(job, limit=None) if self.history_page else self.server.appearance_release.state(job)
                return self._json(200, paginate(data, self.history_page))
            except ValueError as error:
                return self._json(400, {'error': str(error)})
        if self.path == '/api/fresh':
            data = self.server.provision.fresh_state(limit=None) if self.history_page else self.server.provision.fresh_state()
            groups = getattr(self.server, 'playbook_groups', None)
            data['playbook_groups'] = groups.groups() if groups else []
            return self._json(200, paginate(data, self.history_page, fresh=True))
        match = re.fullmatch(r'/api/fresh/run/([a-f0-9]{32})', self.path)
        if match:
            try:
                return self._json(200, self.server.provision.record(match[1]))
            except FileNotFoundError:
                return self._json(404, {'error': 'Installationsauftrag nicht gefunden.'})
            except (OSError, ValueError):
                return self._json(503, {'error': 'Installationsauftrag konnte nicht gelesen werden.'})
        if self.path.startswith('/api/fresh/log/'):
            try:
                output = self.server.provision.output(self.path.rsplit('/',1)[-1])
            except FileNotFoundError:
                return self._json(404, {'error': 'Installationsprotokoll nicht gefunden.'})
            return self._json(200, {'output':output})
        if self.path == '/api/maintenance-runs':
            from maintenance.controller import history, targets
            try:
                data = {'runs': history()[:None if self.history_page else 20], 'enrolled': sorted(targets()),
                        'busy': self.server.deployments.lock.locked()}
                if self.server.backups.profile == 'apps':
                    from maintenance import teacher_controller
                    data['teacher'] = {'configuration': teacher_controller.configuration(),
                                       'runs': teacher_controller.history()[:10]}
                return self._json(200, paginate(data, self.history_page))
            except (OSError, ValueError):
                return self._json(503, {'error': 'Zentrale Wartungsergebnisse konnten nicht gelesen werden.'})
        if self.path == '/api/maintenance-hosts':
            try:
                return self._json(200, {'hosts': self.server.provision.maintenance_hosts()})
            except (OSError, ValueError):
                return self._json(503, {'error': 'Wartungsregistrierung konnte nicht gelesen werden.'})
        if self.path == '/api/maintenance' or self.path.startswith('/api/maintenance/'):
            try:
                host = self.path.rsplit('/', 1)[-1] if self.path.startswith('/api/maintenance/') else 'LAB-08A'
                return self._json(200, self.server.provision.maintenance_state(host))
            except (OSError,ValueError,subprocess.SubprocessError):
                return self._json(503, {'error': 'Wartungsstatus konnte nicht geladen werden.'})
        if self.path == '/api/appearance/rollout':
            data = self.server.appearance_release.state(limit=None) if self.history_page else self.server.appearance_release.state()
            return self._json(200, paginate(data, self.history_page))
        if self.path.startswith('/api/appearance/rollout/log/'):
            try:
                run_id, host = self.path.removeprefix('/api/appearance/rollout/log/').split('/')
                output = self.server.appearance_release.output(run_id, host)
            except (FileNotFoundError, ValueError):
                return self._json(404, {'error': 'Rechnerprotokoll nicht gefunden.'})
            return self._json(200, {'output': output})
        if self.path == '/api/appearance':
            return self._json(200, self.server.appearance.choices())
        if self.path.startswith('/api/appearance/preview/'):
            try:
                image = self.server.appearance.preview(self.path.removeprefix('/api/appearance/preview/'))
                return self._send(200, image, 'image/jpeg')
            except FileNotFoundError:
                return self._json(404, {'error': 'Bildvorschau nicht mehr vorhanden. Bitte Bilder neu laden.'})
            except (OSError, ValueError, subprocess.SubprocessError):
                return self._json(503, {'error': 'Bildvorschau konnte nicht geladen werden.'})
        if self.path == '/api/backup/cleanup':
            if not self._admin():
                return
            try:
                return self._json(200,self.server.backups.cleanup_plan())
            except (OSError,ValueError):
                return self._json(503,{'error':'Bereinigungsvorschau konnte nicht sicher gelesen werden.'})
        if self.path.startswith('/api/backup/download/'):
            if not self._admin():
                return
            started = False
            try:
                with self.server.backups.download(self.path.removeprefix('/api/backup/download/')) as (run, archive_source):
                    name = run['file'].removesuffix('.tar.gz.gpg')+'.zip'
                    self.close_connection = True
                    self._headers(200, 'application/zip', headers=[('Content-Disposition', f'attachment; filename="{name}"'),
                                                                  ('Connection', 'close')])
                    started = True
                    if self.command != 'HEAD':
                        self.server.backups.write_zip(run, archive_source, self.wfile)
                return
            except (OSError, ValueError) as error:
                if started:
                    self.close_connection = True
                    return
                return self._json(404 if isinstance(error, FileNotFoundError) else 400,
                                  {'error': str(error) if isinstance(error, ValueError) else 'Sicherungsdownload nicht verfügbar.'})
        if self.path == '/api/backup':
            return self._json(200, paginate(self.server.backups.state(limit=None if self.history_page else 20), self.history_page))
        if self.path == '/api/intake' and self.server.provision.intake:
            return self._json(200, self.server.provision.intake.state())
        if self.path == '/api/room':
            try:
                runs = self.server.room.history(limit=None) if self.history_page else self.server.room.history()
                access, capabilities = self.server.room.capabilities()
                return self._json(200, paginate(dict(actions=ACTIONS, scopes=SCOPES, capabilities=CAPABILITIES,
                                           target_capabilities=capabilities, access=access,
                                           runs=runs, busy=self.server.deployments.lock.locked()), self.history_page))
            except (OSError, ValueError):
                return self._json(503, {'error': 'APP-Steuerzugänge konnten nicht sicher gelesen werden.'})
        if self.path == '/api/pilot':
            runs = self.server.deployments.reviewable_history(limit=None) if self.history_page else self.server.deployments.reviewable_history()
            return self._json(200, paginate(dict(catalog(), runs=runs), self.history_page))
        if self.path.startswith('/api/pilot/log/'):
            try:
                output = self.server.deployments.output(self.path.rsplit('/', 1)[-1])
            except FileNotFoundError:
                return self._json(404, {'error': 'Lauf nicht gefunden.'})
            return self._json(200, {'output': output})
        if self.path == '/api/state':
            return self._json(200, self.server.dashboard.state())
        if self.path in ASSETS:
            filename, kind = ASSETS[self.path]
            body = (Path(__file__).parent / filename).read_bytes()
            if filename == 'index.html':
                body = body.replace(b'__BASE_PATH__', self.server.base_path.encode())
                body = body.replace(b'__THEME_SCRIPT__',THEME_SCRIPT.encode()).replace(b'__THEME_STYLESHEET__',THEME_STYLESHEET.encode())
                body = body.replace(b'__THEME_PICKER__',THEME_PICKER.encode())
                apps = self.server.backups.profile == 'apps'
                body = body.replace(b'__CONTROLLER_LABEL__', ('APP-Server' if apps else 'LAB-Teacher').encode())
                user = getattr(self, 'identity', dict(role='admin', name='Lokaler Admin', source='local'))
                csrf = self.session['csrf'] if getattr(self, 'session', None) else ''
                label = user['name'] + (' · Administrator' if user['role'] == 'admin' else ' · Kollegium')
                action = ('<a class="logout-link" id="auth-logout" href="' + self.server.base_path + '/">Abmelden</a>' if user['source'] == 'oidc' else
                          '<a href="' + self.server.base_path + '/oidc/login">Mit IServ anmelden</a>' if getattr(self.server, 'auth', None) else '')
                body = body.replace(b'__ROLE__', user['role'].encode())
                body = body.replace(b'__CSRF__', html.escape(csrf, quote=True).encode())
                body = body.replace(b'__SESSION_LABEL__', html.escape(label).encode())
                body = body.replace(b'__SESSION_ACTION__', action.encode())
                body = body.replace(b'__FOOTER__', FOOTER.encode())
            return self._send(200, body, kind)
        self._json(404, {'error': 'Seite nicht gefunden.'})

    def do_POST(self):
        if not self._local_path():
            return self._json(404, {'error': 'Aktion nicht verfügbar.'})
        if not self._trusted_request(require_origin=True):
            return self._json(403, {'error': 'Anfrage muss von der LAB-Weboberfläche kommen.'})
        if not self._authorized():
            return
        if not self._csrf():
            return
        if self.path == '/logout' and getattr(self, 'session', None):
            self.server.auth.logout(cookie_value(self.headers.get('Cookie', ''), SESSION_COOKIE))
            return self._send(200, json.dumps({'redirect': self.server.base_path + '/'}).encode(),
                              'application/json; charset=utf-8', headers=[('Set-Cookie', cookie(SESSION_COOKIE, '', 0, self.server.base_path))])
        # Verified Kollegium sessions may run this one fixed-inventory workflow.
        # Authentication, origin and CSRF checks above apply to it as usual.
        room_allowed = self.path == '/api/room/start' and getattr(self, 'identity', {}).get('role') == 'reader'
        if not room_allowed and not self._admin():
            return
        if self.path=='/api/software/package/upload':
            from dashboard.deb_client import MAX_DEB
            try:
                length=int(self.headers.get('Content-Length','0'))
                if self.headers.get('Transfer-Encoding') or not 0<length<=MAX_DEB:
                    return self._json(413,dict(error='Bitte eine .deb-Datei bis 512 MiB auswählen.'))
                if self.headers.get_content_type()!='application/octet-stream':
                    raise ValueError('Debian-Paketdatei als Datei hochladen.')
                name=urllib.parse.unquote_to_bytes(self.headers.get('X-LAB-Package-Name','')).decode('utf-8')
                lock=self.server.deployments.lock
                if not lock.acquire(blocking=False):return self._json(409,dict(error='Ein Verwaltungsauftrag läuft bereits.'))
                try:return self._json(201,self.server.software.own.upload(self.rfile,length,name))
                finally:lock.release()
            except ValueError as error:
                return self._json(400,dict(error=str(error)))
            except (OSError,subprocess.SubprocessError):
                return self._json(503,dict(error='Die Paketdatei konnte nicht gespeichert werden.'))
        if self.path == '/api/appearance/upload':
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if self.headers.get('Transfer-Encoding') or not 0 < length <= MAX_IMAGE_UPLOAD:
                    return self._json(413, {'error': 'Bitte ein Bild mit höchstens 48 MiB hochladen.'})
                if self.headers.get_content_type() != 'application/octet-stream':
                    return self._json(400, {'error': 'Bilddatei als Datei hochladen.'})
                raw_name = self.headers.get('X-LAB-Image-Name', '')
                if len(raw_name) > 768:
                    raise ValueError('Dateiname ist zu lang.')
                name = urllib.parse.unquote_to_bytes(raw_name).decode('utf-8')
                operation_lock = self.server.deployments.lock
                if not operation_lock.acquire(blocking=False):
                    return self._json(409, {'error': 'Ein anderer Verwaltungsauftrag läuft bereits.'})
                try:
                    image = self.rfile.read(length)
                    if len(image) != length:
                        raise ValueError('Bilddatei wurde nicht vollständig übertragen.')
                    return self._json(201, self.server.appearance.upload(name, image))
                finally:
                    operation_lock.release()
            except ValueError as error:
                return self._json(400, {'error': str(error) if not isinstance(error, UnicodeError) else 'Ungültiger Dateiname.'})
            except (OSError, subprocess.SubprocessError):
                return self._json(503, {'error': 'Das Bild konnte nicht gespeichert werden.'})
        try:
            length = int(self.headers.get('Content-Length', '0'))
            limit = 16384 if self.path.startswith(('/api/software/', '/api/playbook-groups/')) else 4096 if self.path in ('/api/fresh/bootstrap', '/api/intake/read', '/api/intake/setup', '/api/intake/read-many', '/api/intake/setup-many') else 1024
            if not 0 < length <= limit or self.headers.get_content_type() != 'application/json':
                raise ValueError('Ungültige Anfrage.')
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError('Ungültige Anfrage.')
            if self.path.startswith('/api/playbook-groups/'):
                groups = self.server.playbook_groups
                if self.path == '/api/playbook-groups/save' and set(payload) == {'identity', 'name', 'steps'}:
                    return self._json(200, groups.save_group(**payload))
                if self.path == '/api/playbook-groups/delete' and set(payload) == {'identity', 'confirmed'}:
                    groups.delete_group(**payload)
                    return self._json(200, dict(deleted=True))
                if self.path == '/api/playbook-groups/start' and set(payload) == {'group_id', 'targets', 'confirmed'}:
                    run = groups.start(**payload)
                    return self._json(202 if run else 409, run or {'error': 'Ein Verwaltungsauftrag läuft bereits.'})
                raise ValueError('Ungültiger Gruppenauftrag.')
            if self.path.startswith('/api/software/'):
                software = self.server.software
                if self.path=='/api/software/teacher/check' and not payload:
                    if not software.deployments.lock.acquire(blocking=False):
                        return self._json(409, dict(error='Ein Verwaltungsauftrag läuft bereits.'))
                    try:
                        return self._json(200, software.teacher.verify())
                    finally:
                        software.deployments.lock.release()
                if self.path=='/api/software/catalog' and not payload:
                    return self._json(202,dict(started=software.refresh_catalog()))
                if self.path=='/api/software/inventory' and set(payload)=={'targets'}:
                    return self._json(202,dict(started=software.refresh_inventory(payload['targets'])))
                if self.path=='/api/software/profile' and set(payload)=={'id','name','packages','baseline'}:
                    return self._json(200,software.save_profile(payload['id'],payload['name'],payload['packages'],payload['baseline']))
                if self.path=='/api/software/delete' and set(payload)=={'id','confirmed'} and payload['confirmed'] is True:
                    software.delete_profile(payload['id'])
                    return self._json(200,dict(deleted=True))
                if self.path=='/api/software/plan' and set(payload)=={'packages','targets','profile_id'}:
                    run=software.create_plan(payload['packages'],payload['targets'],payload['profile_id'])
                elif self.path=='/api/software/start' and set(payload)=={'run_id','confirmed'}:
                    run=software.start(**payload)
                elif self.path=='/api/software/review' and set(payload)=={'run_id','confirmed'}:
                    return self._json(200,software.review(**payload))
                else:
                    raise ValueError('Ungültiger Softwareauftrag.')
                return self._json(202 if run else 409,run or {'error':'Ein Verwaltungsauftrag läuft bereits.'})
            if self.path.startswith('/api/intake/') and self.server.provision.intake:
                intake = self.server.provision.intake
                if self.path == '/api/intake/read-many' and set(payload) == {
                    'hosts', 'ssh_password', 'image_confirmed', 'replacement_fingerprints',
                }:
                    run = intake.start_many(**payload)
                elif self.path == '/api/intake/setup-many' and set(payload) == {
                    'targets', 'ssh_password', 'sudo_password', 'confirmed', 'student',
                }:
                    run = intake.setup_many(**payload)
                elif self.path == '/api/intake/read' and set(payload) == {
                    'host', 'ssh_password', 'student', 'image_confirmed', 'replacement_fingerprint',
                }:
                    run = intake.start(**payload)
                elif self.path == '/api/intake/setup' and set(payload) - {'student'} == {
                    'run_id', 'lab_interface', 'ssh_password', 'sudo_password', 'confirmed',
                }:
                    run = intake.setup(**payload)
                elif self.path == '/api/intake/accept' and set(payload) == {'run_id', 'confirmed'}:
                    run = intake.accept_pilot(**payload)
                else:
                    raise ValueError('Ungültige Geräteaufnahme.')
                return self._json(202 if run else 409, run or {'error': 'Ein Verwaltungsauftrag läuft.'})
            if self.path == '/api/plan' and set(payload) == {'host'} and isinstance(payload['host'], str):
                return self._json(200, deployment_plan(payload['host']))
            if self.path == '/api/pilot/plan' and set(payload) == {'host', 'job'}:
                if all(isinstance(value, str) for value in payload.values()):
                    return self._json(200, self.server.deployments.plan(**payload))
            if self.path == '/api/pilot/review' and set(payload) == {'run_id', 'decision', 'checks', 'note'}:
                if all(isinstance(payload[key], str) for key in ('run_id', 'decision', 'note')):
                    return self._json(200, self.server.deployments.review(**payload))
            if self.path == '/api/pilot/start' and set(payload) == {'host', 'job', 'revision', 'token'}:
                if all(isinstance(value, str) for value in payload.values()):
                    run = self.server.deployments.start(**payload)
                    if run:
                        return self._json(202, run)
                    return self._json(409, {'error': 'Ein Deploy läuft bereits.'})
            if self.path == '/api/rollout/start' and set(payload) == {'job','run_id','retry','confirmed'}:
                if all(isinstance(payload[key],str) for key in ('job','run_id','retry')):
                    run=self.server.appearance_release.start(
                        payload['run_id'], False, payload['confirmed'], job=payload['job'], retry=payload['retry'])
                    return self._json(202 if run else 409, run or {'error':'Ein Verwaltungsauftrag läuft.'})
            if self.path == '/api/fresh/start' and set(payload) == {'host','confirmed'} and isinstance(payload['host'],str):
                run=self.server.provision.fresh_start([{'host': payload['host'], 'veyon': False}], payload['confirmed'])
                return self._json(202 if run else 409, run or {'error':'Ein Verwaltungsauftrag läuft.'})
            if self.path == '/api/fresh/start' and set(payload) in ({'targets','confirmed'}, {'targets','confirmed','group_id'}):
                run=self.server.provision.fresh_start(**payload)
                return self._json(202 if run else 409, run or {'error':'Ein Verwaltungsauftrag läuft.'})
            if self.path == '/api/fresh/bootstrap' and set(payload) == {
                'host', 'fingerprint', 'ssh_password', 'sudo_password', 'confirmed',
            }:
                run=self.server.provision.bootstrap_start(**payload)
                return self._json(202 if run else 409, run or {'error':'Ein Verwaltungsauftrag läuft.'})
            if self.path in ('/api/fresh/retry', '/api/fresh/stop') and set(payload) == {'run_id', 'confirmed'}:
                if isinstance(payload['run_id'], str):
                    method = self.server.provision.retry if self.path.endswith('/retry') else self.server.provision.stop
                    run = method(**payload)
                    return self._json(202 if run else 409, run or {'error':'Ein Verwaltungsauftrag läuft.'})

            if self.path == '/api/maintenance/start' and set(payload) in ({'confirmed'}, {'host', 'confirmed'}, {'hosts', 'confirmed'}):
                if 'host' in payload and not isinstance(payload['host'], str):
                    raise ValueError('Ungültiger Wartungsclient.')
                run=self.server.provision.maintenance_start(**payload)
                return self._json(202 if run else 409, run or {'error':'Ein Verwaltungsauftrag läuft.'})
            if self.path == '/api/appearance/rollout/start' and set(payload) in (
                {'run_id', 'refresh', 'confirmed'}, {'run_id', 'refresh', 'confirmed', 'retry'},
            ):
                if isinstance(payload['run_id'], str) and isinstance(payload.get('retry',''),str):
                    run = self.server.appearance_release.start(**payload)
                    if run:
                        return self._json(202, run)
                    return self._json(409, {'error': 'Ein anderer Verwaltungsauftrag läuft bereits.'})
            if self.path == '/api/appearance/select' and set(payload) == {'desktop', 'lock'}:
                if all(isinstance(value, str) for value in payload.values()):
                    operation_lock = self.server.deployments.lock
                    if not operation_lock.acquire(blocking=False):
                        return self._json(409, {'error': 'Ein anderer Verwaltungsauftrag läuft bereits.'})
                    try:
                        return self._json(200, self.server.appearance.select(**payload))
                    finally:
                        operation_lock.release()
            if self.path == '/api/backup/start' and set(payload) in ({'targets'}, {'transfer'}, {'transfer', 'confirmed'}):
                run = self.server.backups.start(targets=payload['targets']) if 'targets' in payload else self.server.backups.start(payload['transfer'])
                if run:
                    return self._json(202, run)
                return self._json(409, {'error': 'Ein anderer Verwaltungsauftrag läuft bereits.'})
            if self.path == '/api/backup/cleanup' and set(payload) == {'token','confirmed'}:
                job = self.server.backups.cleanup(payload['token'],payload['confirmed'])
                return self._json(202 if job else 409,job or {'error':'Ein anderer Verwaltungsauftrag läuft bereits.'})
            if self.path == '/api/backup/confirm' and set(payload) == {'run_id', 'confirmed'}:
                if payload['confirmed'] is not True or not isinstance(payload['run_id'], str):
                    raise ValueError('Wiederherstellung ausdrücklich bestätigen.')
                return self._json(200, self.server.backups.confirm(payload['run_id']))
            if self.path == '/api/room/start' and set(payload) in (
                    {'action', 'scope', 'confirmed'}, {'action', 'targets', 'confirmed'}):
                run = self.server.room.start(**payload)
                if run:
                    return self._json(202, run)
                return self._json(409, {'error': 'Ein anderer Verwaltungsauftrag läuft bereits.'})
            if self.path == '/api/refresh' and not payload:
                started = self.server.dashboard.refresh()
                return self._json(202 if started else 409, {'started': started})
        except ValueError as error:
            return self._json(400, {'error': str(error)})
        except FileNotFoundError:
            return self._json(404, {'error': 'Lauf nicht gefunden.'})
        except (OSError, subprocess.SubprocessError):
            return self._json(503, {'error': 'Die Aktion konnte nicht verarbeitet werden. Bitte den Auftragsstatus und das Protokoll prüfen.'})
        self._json(404, {'error': 'Aktion nicht verfügbar.'})

    def log_request(self, code='-', size='-'):
        # Callback query strings contain authorization codes and must never be logged.
        path = urllib.parse.urlsplit(self.path).path[:120].encode('unicode_escape').decode()
        print(f'HTTP {self.command} {path} {code}', file=sys.stderr, flush=True)

    def log_message(self, format, *args):
        # Avoid arbitrary request lines/headers in parser error diagnostics.
        pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--lab08b-known-hosts', type=Path)
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--https-host', help='DNS-Name des lokalen HTTPS-Reverse-Proxys')
    parser.add_argument('--state-dir', type=Path, required=True)
    parser.add_argument('--base-path', default='', help='Öffentlicher URL-Prefix, z.B. /cisco-lab')
    parser.add_argument('--backup-profile', choices=('teacher', 'apps'), default='teacher')
    args = parser.parse_args()
    if args.base_path and not re.fullmatch(r'/[a-z0-9]+(?:-[a-z0-9]+)*', args.base_path):
        parser.error('--base-path muss ein einfacher absoluter URL-Prefix sein.')
    if args.https_host and not re.fullmatch(
        r'(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}',
        args.https_host,
    ):
        parser.error('--https-host muss ein kleingeschriebener DNS-Name ohne Port sein.')
    os.umask(0o077)
    state_dir = args.state_dir.expanduser()
    state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    if state_dir.stat().st_mode & 0o077 or state_dir.stat().st_uid != os.getuid():
        parser.error('Zustandsverzeichnis muss ausschließlich dem Dienstkonto gehören.')
    password = state_dir / 'passwort'
    oidc_path = ROOT / 'local-private/oidc.json'
    auth = Auth(state_dir / 'auth', oidc_configuration(oidc_path)) if args.backup_profile == 'apps' and oidc_path.exists() else None
    dashboard = Dashboard(args.report.expanduser(), args.lab08b_known_hosts,
                          state_dir / 'client-report-revision')
    with (state_dir / 'server.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if (not auth or auth.local_login_enabled) and not password.exists():
            with password.open('x') as file:
                file.write(secrets.token_urlsafe(32) + '\n')
        try:
            if not auth or auth.local_login_enabled:
                read_credential(password)
        except (OSError, ValueError):
            parser.error('Passwortdatei ist nicht lesbar, nicht privat oder enthält kein gültiges Passwort.')
        with ThreadingHTTPServer(('127.0.0.1', args.port), Handler) as server:
            server.dashboard = dashboard
            server.appearance = Appearance(state_dir / 'appearance')
            server.deployments = Deployments(state_dir / 'deploys', args.lab08b_known_hosts, server.appearance)
            server.appearance_release = AppearanceRelease(state_dir / 'appearance-releases', server.deployments)
            server.provision = Provision(state_dir / 'provision', server.deployments, args.backup_profile)
            server.backups = Backups(state_dir / 'backup', server.deployments, state_dir, args.backup_profile)
            server.room = RoomActions(state_dir / 'room', server.deployments, args.backup_profile)
            server.activity = Activity(state_dir, server.deployments.lock)
            server.software = Software(state_dir / 'software',server.deployments)
            server.playbook_groups = PlaybookGroups(state_dir / 'playbook-groups', server.deployments, server.software)
            server.provision.playbook_groups = server.playbook_groups
            server.classroom = Classroom()
            server.presence = Presence()
            server.password_path = password
            server.auth = auth
            server.https_host = args.https_host
            server.base_path = args.base_path
            if args.backup_profile == 'apps' and not (state_dir/'software/catalog.sqlite').is_file():
                server.software.refresh_catalog()
            print(f'Local: http://127.0.0.1:{server.server_port}', flush=True)
            server.serve_forever()


if __name__ == '__main__':
    main()
