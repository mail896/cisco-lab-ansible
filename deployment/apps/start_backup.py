"""Start and wait for one authenticated LAB-only APP backup over loopback."""
import http.client
import json
from pathlib import Path
import sys
import time

from dashboard.service_access import authorization

HOST = '127.0.0.1:8765'


def request(method, path, credential, payload=None):
    connection = http.client.HTTPConnection('127.0.0.1', 8765, timeout=20)
    headers = {'Authorization': credential, 'Host': HOST}
    body = None
    if payload is not None:
        body = json.dumps(payload).encode()
        headers.update({'Content-Type': 'application/json', 'Origin': 'http://' + HOST})
    try:
        connection.request(method, '/cisco-lab' + path, body=body, headers=headers)
        response = connection.getresponse()
        data = json.loads(response.read())
        if response.status not in (200, 202):
            raise RuntimeError(f'LAB-Backup-API antwortet mit HTTP {response.status}: {data.get("error", "Fehler")}')
        return data
    finally:
        connection.close()


def main():
    credential = authorization(PASSWORD.parent)
    run = request('POST', '/api/backup/start', credential,
                  {'targets': ['apps', 'mac']})
    run_id = run['id']
    print(f'LAB-Sicherung gestartet: {run_id}', flush=True)
    deadline = time.monotonic() + 1800
    while time.monotonic() < deadline:
        time.sleep(3)
        history = request('GET', '/api/backup', credential)['runs']
        current = next((item for item in history if item['id'] == run_id), None)
        if current is None:
            raise RuntimeError('LAB-Sicherungsprotokoll fehlt.')
        if current['status'] == 'finished':
            if not current.get('copied') or not current.get('remote_sha256_verified'):
                raise RuntimeError('Mac-Kopie wurde nicht bestätigt.')
            print(f'LAB-Sicherung mit bestätigter Mac-Prüfsumme abgeschlossen: '
                  f'{run_id}, {current["size"]} Bytes', flush=True)
            return
        if current['status'] == 'failed':
            raise RuntimeError(current.get('error', 'LAB-Sicherung fehlgeschlagen.'))
    raise TimeoutError(f'LAB-Sicherung läuft nach 30 Minuten noch: {run_id}')


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--confirm':
        credential = authorization(PASSWORD.parent)
        confirmed = request('POST', '/api/backup/confirm', credential,
                            {'run_id': sys.argv[2], 'confirmed': True})
        print('LAB-Sicherung als funktionierend bestätigt: ' + confirmed['id'])
    elif len(sys.argv) == 1:
        main()
    else:
        raise SystemExit('Verwendung: start_backup.py [--confirm RUN_ID]')
