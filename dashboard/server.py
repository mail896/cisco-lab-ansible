"""Local LAB maintenance dashboard and single-client pilot deployments."""
import argparse
import base64
import binascii
import fcntl
import hmac
import os
import re
import secrets
import stat
from collections import Counter
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading

from dashboard.appearance import Appearance
from dashboard.appearance_release import AppearanceRelease
from dashboard.backup import Backups
from dashboard.deploy import Deployments, catalog
from dashboard.provision import ENTRIES, Provision, source
from dashboard.room import ACTIONS, SCOPES, RoomActions
from maintenance.preview import BERLIN, start_allowed

ROOT = Path(__file__).resolve().parents[1]
ASSETS = {
    '/': ('index.html', 'text/html; charset=utf-8'),
    '/app.css': ('app.css', 'text/css; charset=utf-8'),
    '/app.js': ('app.js', 'text/javascript; charset=utf-8'),
    '/catalog.js': ('catalog.js', 'text/javascript; charset=utf-8'),
}
for page in ('/schuelerclients', '/installationsfolge', '/pilotdeploys',
             '/sammelaktionen', '/backup', '/desktopbilder', '/playbooks', '/wartung'):
    ASSETS[page] = ASSETS['/']
HOSTS = tuple(f'LAB-{number:02d}{side}' for number in range(1, 9) for side in 'AB')


class Dashboard:
    def __init__(self, report, known_hosts):
        self.report = report
        self.known_hosts = known_hosts
        self.refresh_lock = threading.Lock()
        self.refresh_error = None

    def state(self):
        try:
            report = json.loads(self.report.read_text())
        except (OSError, ValueError):
            report = {}
        if not isinstance(report, dict):
            report = {}
        clients = report.get('clients', {})
        rows = []
        for host in HOSTS:
            client = clients.get(host, {})
            upgrade = client.get('upgrade', {})
            cleanup = client.get('autoremove', {})
            apt = client.get('automatic_apt') or {}
            rows.append(dict(host=host, action=client.get('action', 'unknown'),
                             updates=len(upgrade.get('install', [])),
                             removals=len(cleanup.get('remove', [])),
                             cleanup=client.get('autoremove_allowed', False),
                             patch=client.get('patch', 'unknown'),
                             apt_enabled=apt.get('upgrade_timer') == 'enabled'))
        return dict(timestamp=report.get('timestamp'), window=start_allowed(datetime.now(BERLIN)),
                    counts=dict(Counter(row['action'] for row in rows)), clients=rows,
                    refreshing=self.refresh_lock.locked(), refresh_error=self.refresh_error)

    def refresh(self):
        if not self.refresh_lock.acquire(blocking=False):
            return False
        self.refresh_error = None
        threading.Thread(target=self._refresh, daemon=True).start()
        return True

    def _refresh(self):
        try:
            command = [sys.executable, '-B', '-m', 'maintenance.preview']
            if self.known_hosts:
                command += ['--lab08b-known-hosts', str(self.known_hosts)]
            result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True,
                                    timeout=285, check=True)
            data = json.loads(result.stdout)
            with tempfile.NamedTemporaryFile(dir=self.report.parent, delete=False,
                                             mode='w', encoding='utf-8') as file:
                temporary = Path(file.name)
                try:
                    json.dump(data, file, ensure_ascii=False, indent=2)
                    file.write('\n')
                    file.flush()
                    temporary.replace(self.report)
                finally:
                    temporary.unlink(missing_ok=True)
        except (OSError, ValueError, subprocess.SubprocessError):
            self.refresh_error = 'Vorschau konnte nicht aktualisiert werden. Letzter Bericht bleibt erhalten.'
        finally:
            self.refresh_lock.release()


def deployment_plan(host):
    if host not in HOSTS:
        raise ValueError('Bitte einen LAB-Client auswählen.')
    args = ['ansible-playbook', '-i', 'inventory.ini', '--limit', host, '--list-tasks', 'fresh-client.yml']
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
    def _request_origin(self):
        port = self.server.server_port
        host = self.headers.get('Host')
        proto = self.headers.get('X-Forwarded-Proto')
        if proto is None and host in {f'127.0.0.1:{port}', f'localhost:{port}'}:
            return f'http://{host}'
        if proto == 'https' and host and host == self.server.https_host:
            return f'https://{host}'
        return None

    def _trusted_request(self, require_origin=False):
        expected = self._request_origin()
        origin = self.headers.get('Origin')
        return expected is not None and (origin == expected or (origin is None and not require_origin))

    def _authorized(self):
        try:
            scheme, token = self.headers.get('Authorization', '').split(' ', 1)
            supplied = base64.b64decode(token, validate=True)
            allowed = scheme.lower() == 'basic' and hmac.compare_digest(
                supplied, read_credential(self.server.password_path)
            )
        except (OSError, ValueError, binascii.Error):
            allowed = False
        if not allowed:
            self._send(401, b'Anmeldung erforderlich.', 'text/plain; charset=utf-8')
        return allowed

    def _send(self, status, body, kind):
        self.send_response(status)
        if status == 401:
            self.send_header('WWW-Authenticate', 'Basic realm="CISCO-LAB lokal", charset="UTF-8"')
        self.send_header('Content-Type', kind)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'self'; object-src 'none'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status, data):
        self._send(status, json.dumps(data, ensure_ascii=False).encode(), 'application/json; charset=utf-8')

    def do_GET(self):
        if not self._trusted_request():
            return self._json(403, {'error': 'Nur lokaler Zugriff.'})
        if not self._authorized():
            return
        if self.path == '/api/playbooks':
            return self._json(200, ENTRIES)
        if self.path.startswith('/api/playbooks/'):
            entry = self.path.rsplit('/',1)[-1]
            try:
                content = source(entry)
            except FileNotFoundError:
                return self._json(404, {'error': 'Playbook nicht gefunden.'})
            return self._json(200, {'content': content, 'entry': ENTRIES[entry]})
        if self.path.startswith('/api/rollout/state/'):
            job = self.path.rsplit('/',1)[-1]
            try:
                return self._json(200, self.server.appearance_release.state(job))
            except ValueError as error:
                return self._json(400, {'error': str(error)})
        if self.path == '/api/fresh':
            return self._json(200, self.server.provision.fresh_state())
        if self.path.startswith('/api/fresh/log/'):
            try:
                output = self.server.provision.output(self.path.rsplit('/',1)[-1])
            except FileNotFoundError:
                return self._json(404, {'error': 'Installationsprotokoll nicht gefunden.'})
            return self._json(200, {'output':output})
        if self.path == '/api/maintenance':
            try:
                return self._json(200, self.server.provision.maintenance_state())
            except (OSError,ValueError,subprocess.SubprocessError):
                return self._json(503, {'error': 'Wartungsstatus konnte nicht geladen werden.'})
        if self.path == '/api/appearance/rollout':
            return self._json(200, self.server.appearance_release.state())
        if self.path.startswith('/api/appearance/rollout/log/'):
            try:
                run_id, host = self.path.removeprefix('/api/appearance/rollout/log/').split('/')
                output = self.server.appearance_release.output(run_id, host)
            except (FileNotFoundError, ValueError):
                return self._json(404, {'error': 'Rechnerprotokoll nicht gefunden.'})
            return self._json(200, {'output': output})
        if self.path == '/api/appearance':
            return self._json(200, self.server.appearance.choices())
        if self.path == '/api/backup':
            return self._json(200, {'runs': self.server.backups.history()})
        if self.path == '/api/room':
            return self._json(200, dict(actions=ACTIONS, scopes=SCOPES, runs=self.server.room.history()))
        if self.path == '/api/pilot':
            return self._json(200, dict(catalog(), runs=self.server.deployments.history()))
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
            return self._send(200, Path(__file__).with_name(filename).read_bytes(), kind)
        self._json(404, {'error': 'Seite nicht gefunden.'})

    def do_POST(self):
        if not self._trusted_request(require_origin=True):
            return self._json(403, {'error': 'Anfrage muss von der LAB-Weboberfläche kommen.'})
        if not self._authorized():
            return
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 1024 or self.headers.get_content_type() != 'application/json':
                raise ValueError('Ungültige Anfrage.')
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError('Ungültige Anfrage.')
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
                run=self.server.provision.fresh_start(**payload)
                return self._json(202 if run else 409, run or {'error':'Ein Verwaltungsauftrag läuft.'})
            if self.path == '/api/maintenance/start' and set(payload) == {'confirmed'}:
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
            if self.path == '/api/backup/start' and set(payload) == {'transfer', 'confirmed'}:
                if payload['confirmed'] is not True:
                    raise ValueError('Bitte bestätigen, dass der Wiederherstellungsschlüssel extern gesichert ist.')
                run = self.server.backups.start(payload['transfer'])
                if run:
                    return self._json(202, run)
                return self._json(409, {'error': 'Ein anderer Verwaltungsauftrag läuft bereits.'})
            if self.path == '/api/room/start' and set(payload) == {'action', 'scope', 'confirmed'}:
                if all(isinstance(payload[key], str) for key in ('action', 'scope')):
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
            return self._json(503, {'error': 'Ansible-Auftrag konnte nicht verarbeitet werden.'})
        self._json(404, {'error': 'Aktion nicht verfügbar.'})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--lab08b-known-hosts', type=Path)
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--https-host', help='DNS-Name des lokalen HTTPS-Reverse-Proxys')
    parser.add_argument('--state-dir', type=Path, required=True)
    args = parser.parse_args()
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
    dashboard = Dashboard(args.report.expanduser(), args.lab08b_known_hosts)
    with (state_dir / 'server.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if not password.exists():
            with password.open('x') as file:
                file.write(secrets.token_urlsafe(32) + '\n')
        try:
            read_credential(password)
        except (OSError, ValueError):
            parser.error('Passwortdatei ist nicht lesbar, nicht privat oder enthält kein gültiges Passwort.')
        with ThreadingHTTPServer(('127.0.0.1', args.port), Handler) as server:
            server.dashboard = dashboard
            server.appearance = Appearance(state_dir / 'appearance')
            server.deployments = Deployments(state_dir / 'deploys', args.lab08b_known_hosts, server.appearance)
            server.appearance_release = AppearanceRelease(state_dir / 'appearance-releases', server.deployments)
            server.provision = Provision(state_dir / 'provision', server.deployments)
            server.backups = Backups(state_dir / 'backup', server.deployments, state_dir)
            server.room = RoomActions(state_dir / 'room', server.deployments)
            server.password_path = password
            server.https_host = args.https_host
            print(f'Local: http://127.0.0.1:{server.server_port}', flush=True)
            server.serve_forever()


if __name__ == '__main__':
    main()
