"""Loopback prototype for reviewing LAB maintenance and Ansible plans."""
import argparse
from collections import Counter
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading

from maintenance.preview import BERLIN, start_allowed

ROOT = Path(__file__).resolve().parents[1]
ASSETS = {
    '/': ('index.html', 'text/html; charset=utf-8'),
    '/app.css': ('app.css', 'text/css; charset=utf-8'),
    '/app.js': ('app.js', 'text/javascript; charset=utf-8'),
}
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


class Handler(BaseHTTPRequestHandler):
    def _trusted_request(self):
        port = self.server.server_port
        hosts = {f'127.0.0.1:{port}', f'localhost:{port}'}
        origin = self.headers.get('Origin')
        return self.headers.get('Host') in hosts and (
            origin is None or origin in {f'http://{host}' for host in hosts}
        )

    def _send(self, status, body, kind):
        self.send_response(status)
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
        if self.path == '/api/state':
            return self._json(200, self.server.dashboard.state())
        if self.path in ASSETS:
            filename, kind = ASSETS[self.path]
            return self._send(200, Path(__file__).with_name(filename).read_bytes(), kind)
        self._json(404, {'error': 'Seite nicht gefunden.'})

    def do_POST(self):
        if not self._trusted_request():
            return self._json(403, {'error': 'Nur lokaler Zugriff.'})
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 1024 or self.headers.get_content_type() != 'application/json':
                raise ValueError('Ungültige Anfrage.')
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError('Ungültige Anfrage.')
            if self.path == '/api/plan' and set(payload) == {'host'} and isinstance(payload['host'], str):
                return self._json(200, deployment_plan(payload['host']))
            if self.path == '/api/refresh' and not payload:
                started = self.server.dashboard.refresh()
                return self._json(202 if started else 409, {'started': started})
        except ValueError as error:
            return self._json(400, {'error': str(error)})
        except (OSError, subprocess.SubprocessError):
            return self._json(503, {'error': 'Ansible-Plan konnte nicht gelesen werden.'})
        self._json(404, {'error': 'Aktion nicht verfügbar.'})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--lab08b-known-hosts', type=Path)
    parser.add_argument('--port', type=int, default=8765)
    args = parser.parse_args()
    dashboard = Dashboard(args.report.expanduser(), args.lab08b_known_hosts)
    with ThreadingHTTPServer(('127.0.0.1', args.port), Handler) as server:
        server.dashboard = dashboard
        print(f'Local: http://127.0.0.1:{server.server_port}', flush=True)
        server.serve_forever()


if __name__ == '__main__':
    main()
