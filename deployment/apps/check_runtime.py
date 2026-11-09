"""Small read-only APP smoke check, with image conversion only in private scratch."""
import http.client
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import time
from unittest.mock import patch

from PIL import Image
from dashboard import appearance
from dashboard.backup import apps_system_sources
from dashboard.service_access import authorization


def main():
    credential = authorization(Path('/var/lib/cisco-lab/.local/state/cisco-lab-dashboard'))

    def request(path, *, payload=None, authenticated=True, origin=None, expected=200):
        connection = http.client.HTTPConnection('127.0.0.1', 8765, timeout=25)
        headers = {'Host': '127.0.0.1:8765'}
        if authenticated:
            headers['Authorization'] = credential
        if payload is not None:
            headers.update({'Content-Type': 'application/json',
                            'Origin': origin or 'http://127.0.0.1:8765'})
        started = time.monotonic()
        try:
            connection.request('POST' if payload is not None else 'GET', path,
                               body=json.dumps(payload) if payload is not None else None,
                               headers=headers)
            response = connection.getresponse()
            body = response.read()
            if response.status != expected:
                raise RuntimeError(f'{path}: HTTP {response.status}, erwartet {expected}')
            if response.getheader('Cache-Control') != 'no-store':
                raise RuntimeError(f'{path}: Cache-Schutz fehlt')
            print(f'{path}: {response.status} ({time.monotonic() - started:.2f}s)')
            return json.loads(body) if response.getheader('Content-Type', '').startswith('application/json') else None
        finally:
            connection.close()

    for path in ('/', '/installationsfolge', '/wartung', '/backup', '/desktopbilder',
                 '/app.js', '/catalog.js', '/api/state', '/api/fresh', '/api/maintenance-hosts',
                 '/api/maintenance-runs', '/api/appearance', '/api/backup', '/api/room', '/api/pilot'):
        request('/cisco-lab' + path)
    for job in ('firefox', 'serial', 'session', 'appearance', 'cisco'):
        request('/cisco-lab/api/rollout/state/' + job)
    plan = request('/cisco-lab/api/plan', payload={'host': 'LAB-08A'})
    print('Lokale Baseline-Aufgabenplanung:', len(plan['tasks']), 'Einträge; keine Clientaktion.')
    request('/cisco-lab/api/state', authenticated=False, expected=401)
    request('/api/state', expected=404)
    request('/cisco-lab/api/refresh', payload={}, origin='https://example.invalid', expected=403)
    print('LAB-Systemdateien für Sicherung lesbar:', len(apps_system_sources()))
    with TemporaryDirectory(prefix='lab-image-check-', dir=Path.home()) as temporary:
        scratch = Path(temporary)
        folder = scratch / 'images'; folder.mkdir()
        Image.new('RGB', (64, 36), '#17263a').save(folder / 'fixture.png')
        with patch.object(appearance, 'FOLDER', folder):
            manager = appearance.Appearance(scratch / 'state')
            manager.select('fixture.png', 'fixture.png')
            if not all(path.is_file() for _, path in manager.active().values()):
                raise RuntimeError('Bildnormalisierung unter Dienstkonto fehlgeschlagen')
    print('Bildnormalisierung unter LAB-Dienstkonto erfolgreich; Testablage entfernt.')


if __name__ == '__main__':
    main()
