"""APP follow-up for Teacher, using a dedicated key and closed remote commands."""
import argparse
from datetime import datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import time
import uuid

from dashboard.admission import read_settings
from dashboard.deploy import ROOT
from dashboard.operation_lock import OperationLock
from dashboard.room import RoomActions
from maintenance.preview import BERLIN, start_allowed
from maintenance import teacher_wake

CONFIG = ROOT / 'local-private/teacher-maintenance.json'
KEY = Path.home() / '.ssh/id_ed25519_teacher'
PIN = Path.home() / '.ssh/teacher.known_hosts'
STATE = Path.home() / '.local/state/cisco-lab-dashboard/teacher'
PENDING = STATE / 'pending.json'


def configuration():
    if not CONFIG.exists():
        return {'version': 1, 'enabled': False, 'pilot_verified': False, 's5_verified': False}
    data = read_settings(CONFIG)
    if (set(data) != {'version', 'enabled', 'pilot_verified', 's5_verified'} or data['version'] != 1
            or any(type(data[key]) is not bool for key in ('enabled', 'pilot_verified', 's5_verified'))
            or (data['enabled'] and not (data['pilot_verified'] and data['s5_verified']))):
        raise ValueError('Teacher-Automatik benötigt vollständige Pilot- und S5-Abnahme.')
    return data


def enabled():
    return configuration()['enabled']


def connection(command):
    """Pinned transport shared by the closed Teacher maintenance/software APIs."""
    for path in (KEY, PIN):
        info = path.lstat()
        if not path.is_file() or path.is_symlink() or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError('Privater Teacher-Dienstschlüssel oder Host-Pin fehlt.')
    return [
        '/usr/bin/ssh', '-T', '-F', '/dev/null', '-o', 'BatchMode=yes',
        '-o', 'IdentitiesOnly=yes', '-o', 'IdentityAgent=none', '-o', 'StrictHostKeyChecking=yes',
        '-o', 'GlobalKnownHostsFile=/dev/null', '-o', 'UserKnownHostsFile=' + str(PIN),
        '-o', 'ConnectTimeout=5', '-o', 'ControlMaster=no', '-o', 'ControlPath=none',
        '-i', str(KEY), 'cisco-lab-app@' + teacher_wake.ADDRESS, command,
    ]


def request(command):
    result = subprocess.run(connection(command), capture_output=True, text=True, timeout=25)
    if result.returncode:
        if any(text in result.stderr for text in ('Permission denied', 'Host key verification failed',
                                                 'IDENTIFICATION HAS CHANGED', 'a password is required')):
            raise RuntimeError('Begrenzter Teacher-Zugang oder Hostschlüssel muss geprüft werden.')
        # A shutdown can close the SSH transport after the root receiver has
        # sent a complete acceptance reply. Never infer acceptance from EOF
        # alone; the caller still requires three subsequent offline probes.
        parts = command.split(' ')
        if result.returncode == 255 and len(parts) == 3 and parts[0] in ('poweroff', 'manual-poweroff'):
            try:
                acknowledgement = json.loads(result.stdout)
            except (ValueError, TypeError):
                acknowledgement = None
            if acknowledgement == {'poweroff_requested': True, 'id': parts[1]}:
                return acknowledgement
        if 'LAB-Teacher wird gewartet' in result.stderr:
            return None
        if command == 'status' and not reachable():
            return None
        raise RuntimeError('Teacher-Auftrag abgewiesen: ' + (result.stderr.strip()[-350:] or 'keine Bestätigung'))
    data = json.loads(result.stdout)
    if not isinstance(data, dict):
        raise ValueError('Ungültige Teacher-Empfängerantwort.')
    return data


def reachable():
    try:
        with socket.create_connection((teacher_wake.ADDRESS, 22), timeout=2):
            return True
    except OSError:
        return False


def probe():
    data = request('status')
    if data is not None and (data.get('version') != 1 or data.get('host') != 'LAB-Teacher'
                             or str(uuid.UUID(data.get('boot_id', ''))) != data['boot_id']
                             or type(data.get('occupied')) is not bool
                             or type(data.get('uptime')) not in (int, float) or data['uptime'] < 0
                             or data.get('active') not in ('active', 'activating', 'deactivating', 'inactive', 'failed')):
        raise ValueError('Teacher-Empfänger meldet keine eindeutige Identität.')
    return data


def save(path, data):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode='w', prefix='.teacher-', dir=path.parent, delete=False) as file:
        temporary = Path(file.name)
        try:
            json.dump(data, file, ensure_ascii=False)
            file.flush(); os.fsync(file.fileno())
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def history():
    return sorted((read_settings(path) for path in STATE.glob('run-*.json')),
                  key=lambda record: record['started'], reverse=True)


def today(status):
    return any(record.get('status') == status and not record.get('manual_pilot')
               and datetime.fromisoformat(record['started']).astimezone(BERLIN).date() == datetime.now(BERLIN).date()
               for record in history())


def confirm_poweroff(record):
    offline = 0
    for _ in range(30):
        time.sleep(3)
        offline = 0 if reachable() else offline + 1
        if offline == 3:
            record.update(status='succeeded', poweroff_verified=True,
                          poweroff='Nach bestätigtem Ausschaltauftrag dreimal per SSH nicht erreichbar.')
            return
    raise RuntimeError('Teacher nach Ausschaltauftrag weiterhin erreichbar oder Zustand nicht eindeutig.')


def verified_result(record, data):
    remote, origin = data.get('result') or {}, data.get('request') or {}
    if (origin.get('id') != record['id'] or remote.get('id') != record['id']
            or origin.get('boot_id') != record['boot_id'] or remote.get('boot_id') != record['boot_id']
            or origin.get('woke') is not record['woke']):
        raise RuntimeError('Teacher-Auftrag, Boot-ID oder Startursprung ist nicht eindeutig.')
    if remote.get('status') == 'failed' or data['active'] == 'failed':
        raise RuntimeError('Teacher-Wartung fehlgeschlagen; Rechner bleibt an.')
    if remote.get('status') == 'success':
        if type(remote.get('reboot_required')) is not bool:
            raise RuntimeError('Teacher-Neustartbedarf nicht eindeutig.')
        changed = data['boot_id'] != record['boot_id']
        if changed and not remote['reboot_required']:
            raise RuntimeError('Teacher wurde außerhalb des bestätigten Wartungsauftrags neu gestartet.')
    return remote


def implementation_signature():
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def target_signature():
    data = json.dumps(teacher_wake.configuration(), sort_keys=True, separators=(',', ':')).encode()
    return hashlib.sha256(data).hexdigest()


def activate_after_pilot():
    """Called under the operation lock; only a completed real S5 pilot may enable the phase."""
    if socket.gethostname().split('.')[0] != 'apps':
        raise RuntimeError('Teacher-Freigabe ausschließlich auf APP.')
    current = configuration()
    if current['enabled']:
        return current
    if PENDING.exists():
        raise ValueError('Ein Teacher-Pilot ist noch nicht abgeschlossen.')
    for record in history():
        if (record.get('status') != 'succeeded' or record.get('manual_pilot') is not True
                or record.get('woke') is not True or record.get('initial_reachable') is not False
                or record.get('poweroff_verified') is not True
                or record.get('poweroff_acknowledged') is not True
                or record.get('implementation_sha256') != implementation_signature()
                or record.get('target_sha256') != target_signature()):
            continue
        age = datetime.now(BERLIN) - datetime.fromisoformat(record['finished'])
        result = record.get('result') or {}
        if (not timedelta(0) <= age <= timedelta(days=1) or result.get('status') != 'success'
                or result.get('boot_id') != record.get('boot_id')
                or type(result.get('reboot_required')) is not bool
                or (result['reboot_required'] and record.get('final_boot_id') == record.get('boot_id'))):
            continue
        configuration_data = dict(version=1, enabled=True, pilot_verified=True, s5_verified=True)
        save(STATE / 'activation.json', dict(time=datetime.now(BERLIN).isoformat(), pilot_id=record['id']))
        save(CONFIG, configuration_data)
        return configuration_data
    raise ValueError('Kein aktueller erfolgreicher S5-/Wartungspilot dieses Codes und Teacher-Ziels vorhanden.')


def run(*, manual=False):
    if socket.gethostname().split('.')[0] != 'apps':
        raise RuntimeError('Teacher-Steuerung ausschließlich auf APP.')
    config = configuration()
    if not manual and not config['enabled']:
        return {'status': 'disabled', 'reason': 'Teacher-Automatik noch nicht praktisch freigegeben.'}
    if not manual and today('succeeded'):
        return {'status': 'succeeded', 'reason': 'Teacher heute bereits abgeschlossen.'}
    if not manual and today('failed'):
        return {'status': 'failed', 'reason': 'Teacher-Fehler von heute erfordert Prüfung; kein erneuter Start.'}
    pending = read_settings(PENDING) if PENDING.exists() else None
    if not manual and not pending and not start_allowed(datetime.now(BERLIN)):
        return {'status': 'deferred', 'reason': 'Teacher-Startfenster beendet; kein WOL oder Paketstart.'}
    if pending:
        record = pending
    else:
        record = dict(id=uuid.uuid4().hex, started=datetime.now(BERLIN).isoformat(),
                      status='running', manual_pilot=manual, woke=False,
                      implementation_sha256=implementation_signature(), target_sha256=target_signature())
    try:
        if pending and record.get('stage') == 'poweroff-confirming':
            confirm_poweroff(record)
            return record
        if pending and record.get('stage') == 'poweroff-requesting':
            raise RuntimeError('APP-Neustart während des Ausschaltauftrags. Abschluss muss lesend geprüft werden.')
        if not pending:
            was_online = reachable()
            started = time.monotonic()
            if not was_online:
                config = teacher_wake.configuration()
                RoomActions.wake(config['school_mac'], teacher_wake.route())
            deadline = time.monotonic() + 600
            first = None
            while time.monotonic() < deadline:
                first = probe()
                if first is not None:
                    break
                time.sleep(10)
            if first is None:
                raise RuntimeError('Teacher nach WOL nicht mit begrenztem Zugang erreichbar.')
            record.update(boot_id=first['boot_id'], initial_reachable=was_online,
                          initial_uptime=first['uptime'],
                          woke=not was_online and 0 <= first['uptime'] <= time.monotonic() - started + 3)
            if first['occupied'] or first['active'] not in ('inactive', 'failed'):
                record.update(status='deferred', reason='Teacher belegt oder bereits beschäftigt; keine Abmeldung.')
                return record
            record['stage'] = 'maintenance'
            save(PENDING, record)
            response = request(('pilot' if manual else 'start') + ' ' + record['id'] + ' ' + record['boot_id']
                               + (' woke' if record['woke'] else ' already-on'))
            if not response or response.get('id') != record['id'] or response.get('started') is not True:
                raise RuntimeError('Teacher-Wartungsstart nicht bestätigt.')
        deadline = time.monotonic() + 4 * 3600
        inactive_since = None
        while time.monotonic() < deadline:
            data = probe()
            if data is not None:
                remote = verified_result(record, data)
                completed = remote.get('status') == 'success' and data['active'] == 'inactive'
                if completed and (not remote.get('reboot_required') or data['boot_id'] != record['boot_id']):
                    record.update(result=remote, final_boot_id=data['boot_id'])
                    if record['woke'] and not data['occupied']:
                        record['stage'] = 'poweroff-requesting'
                        save(PENDING, record)
                        response = request('poweroff ' + record['id'] + ' ' + data['boot_id'])
                        if not response or response.get('id') != record['id'] or response.get('poweroff_requested') is not True:
                            raise RuntimeError('Teacher-Abschluss nicht bestätigt.')
                        record.update(stage='poweroff-confirming', poweroff_acknowledged=True)
                        save(PENDING, record)
                        confirm_poweroff(record)
                    else:
                        record.update(status='succeeded', poweroff='Unterlassen: zuvor eingeschaltet oder inzwischen belegt.')
                    break
                if data['active'] == 'inactive':
                    inactive_since = inactive_since or time.monotonic()
                    if time.monotonic() - inactive_since > 150:
                        raise RuntimeError('Teacher-Dienst beendet; Abschluss oder Neustart nicht bestätigt.')
                else:
                    inactive_since = None
            time.sleep(10)
        else:
            raise TimeoutError('Teacher-Wartung nach vier Stunden nicht bestätigt; Paketlauf bleibt unangetastet.')
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        record.update(status='failed', reason=str(error))
    finally:
        record['finished'] = datetime.now(BERLIN).isoformat()
        if record['status'] != 'running':
            save(STATE / ('run-' + record['id'] + '.json'), record)
            PENDING.unlink(missing_ok=True)
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--pilot', action='store_true')
    mode.add_argument('--check', action='store_true', help='Nur begrenzten Systemvorcheck lesen')
    mode.add_argument('--activate-after-pilot', action='store_true', help='Automatik nur nach realem grünem S5-Pilot freigeben')
    args = parser.parse_args()
    if not args.pilot and not args.activate_after_pilot:
        print(json.dumps(request('check') if args.check else probe(), ensure_ascii=False))
        return
    lock = OperationLock(STATE.parent / 'operation.lock')
    if not lock.acquire(blocking=False):
        raise SystemExit('Ein LAB-Verwaltungsauftrag läuft.')
    try:
        result = activate_after_pilot() if args.activate_after_pilot else run(manual=True)
        print(json.dumps(result, ensure_ascii=False))
        if not args.activate_after_pilot and result['status'] != 'succeeded':
            raise SystemExit(1)
    finally:
        lock.release()


if __name__ == '__main__':
    main()
