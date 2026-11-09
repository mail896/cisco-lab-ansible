"""GUI intake: fixed targets, first-use SSH pinning and read-only discovery."""
import base64
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile
import threading
import uuid

from dashboard.admission import HARDWARE, read_settings, validate_hardware, verified_key, write_private
from dashboard.ansible_passwords import command as password_command
from dashboard.deploy import ADMISSIONS, LAB_TARGETS
from maintenance.reservations import reserved

MARKER = 'CISCO_LAB_DEVICE_V1:'
COLLECTOR = Path(__file__).with_name('collect_device.py')
PACKAGE = '/var/lib/cisco-lab/private/packages/packettracer-9.0.deb'
READ_ERRORS = {
    'hostname': 'Der Rechnername passt nicht zum gewählten Client. Clientauswahl und abgeschlossene Imageinstallation prüfen.',
    'image': 'Das erwartete Debian-13-Image wurde nicht erkannt. Erst die IServ-Imageinstallation abschließen.',
    'school-address': 'Die feste Schuladresse wurde nicht eindeutig gefunden. Schulnetzkabel und interne Netzwerkkarte prüfen.',
    'school-route': 'Die Standardroute läuft nicht ausschließlich über die Schulkarte. Schulnetzverbindung und USB-LAB-Konfiguration prüfen.',
    'profiles': 'Für einen Adapter sind mehrere Netzwerkprofile vorhanden. Die Zuordnung muss vor der Einrichtung geklärt werden.',
    'school-profile': 'Für die interne Schulkarte fehlt ein aktives Netzwerkprofil. Schulnetzverbindung am Client prüfen.',
    'usb-adapter': 'Kein USB-LAB-Adapter gefunden. Adapter anschließen und „Gerät prüfen“ erneut wählen.',
    'read-tools': 'Die Geräteinformationen konnten nicht vollständig gelesen werden. Imageinstallation abschließen und erneut prüfen.',
    'ssh-auth': 'SSH-Anmeldung als administrator wurde abgewiesen. Passwort oder SSH-Zulassung dieses Kontos am Client prüfen.',
    'ssh-helper': 'APP konnte die geschützte Passwortübergabe nicht starten. Fehler auf APP prüfen; daraus folgt kein falsches Clientpasswort.',
    'ssh-identity': 'Der SSH-Schlüssel hat sich während der Prüfung geändert. „Gerät prüfen“ erneut wählen und die Neuinstallation bestätigen.',
    'timeout': 'Die Geräteprüfung hat zu lange gedauert. Client und Schulnetzverbindung prüfen und erneut versuchen.',
    'connection': 'Die SSH-Verbindung zum Client ist fehlgeschlagen. Client einschalten und Imageinstallation sowie Schulnetz prüfen.',
    'device-data': 'Die gelesenen Geräte- oder Adapterdaten sind nicht eindeutig. Clientauswahl und Schul-/USB-Adapter prüfen.',
    'failed': 'Geräteprüfung fehlgeschlagen. Passwort, Rechnername und USB-Adapter prüfen und erneut versuchen.',
}


class ReadError(ValueError):
    def __init__(self, code):
        self.code = code if isinstance(code, str) and code in READ_ERRORS else 'failed'
        super().__init__(READ_ERRORS[self.code])


def password_valid(value):
    return (isinstance(value, str) and 0 < len(value.encode()) <= 256
            and value == value.strip() and not any(c in value for c in '\r\n\0'))


def scan_key(address):
    try:
        result = subprocess.run(['/usr/bin/ssh-keyscan', '-T', '5', '-t', 'ed25519', address],
                                capture_output=True, text=True, timeout=8)
    except subprocess.TimeoutExpired as error:
        raise ReadError('timeout') from error
    lines = set(line for line in result.stdout.splitlines() if line and not line.startswith('#'))
    if result.returncode or len(lines) != 1:
        raise ValueError('SSH nicht erreichbar. Client einschalten und Imageinstallation abwarten.')
    fields = lines.pop().split()
    if len(fields) != 3 or fields[:2] != [address, 'ssh-ed25519']:
        raise ValueError('Keine eindeutige ED25519-Identität erhalten.')
    try:
        blob = base64.b64decode(fields[2], validate=True)
        # Reject malformed SSH wire data rather than accepting arbitrary scan text.
        if len(blob) != 51 or blob[:19] != b'\0\0\0\x0bssh-ed25519\0\0\0\x20':
            raise ValueError
    except ValueError as error:
        raise ValueError('Ungültiger SSH-Hostschlüssel.') from error
    fingerprint = 'SHA256:' + base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip('=')
    return ' '.join(fields) + '\n', fingerprint


def read_device(host, pin, password):
    """Ansible raw performs no remote upload, sudo or persistent write."""
    with tempfile.TemporaryDirectory(prefix='lab-intake-') as folder:
        known = Path(folder) / 'known_hosts'
        write_private(known, pin)
        fd = os.memfd_create('lab-intake-password')
        try:
            os.fchmod(fd, 0o600)
            os.write(fd, password.encode())
            settings = dict(ansible_host=LAB_TARGETS[host], ansible_user='administrator', ansible_become=False,
                            ansible_ssh_password_mechanism='ssh_askpass',
                            ansible_ssh_args='-o ControlMaster=no -o ControlPath=none',
                            ansible_ssh_common_args=(
                                '-F /dev/null -o StrictHostKeyChecking=yes -o GlobalKnownHostsFile=/dev/null '
                                f'-o UserKnownHostsFile={shlex.quote(str(known))} -o HostKeyAlgorithms=ssh-ed25519 '
                                '-o PubkeyAuthentication=no -o PreferredAuthentications=password,keyboard-interactive '
                                '-o ConnectTimeout=8 -o ServerAliveInterval=10 -o ServerAliveCountMax=2'))
            remote = shlex.join(['/usr/bin/python3', '-IB', '-c', COLLECTOR.read_text(), host, LAB_TARGETS[host]])
            env = dict(os.environ, ANSIBLE_LOG_PATH='', ANSIBLE_DEBUG='0', ANSIBLE_VERBOSITY='0',
                       ANSIBLE_NOCOLOR='1', ANSIBLE_STDOUT_CALLBACK='minimal')
            try:
                result = subprocess.run(password_command('adhoc', [fd], [host, '-i', 'inventory.ini',
                                         '-m', 'raw', '-a', remote, '-e', json.dumps(settings)]),
                                        cwd=COLLECTOR.parent.parent, env=env, pass_fds=[fd],
                                        stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=110)
            except subprocess.TimeoutExpired as error:
                raise ReadError('timeout') from error
        finally:
            os.close(fd)
    lines = [line[len(MARKER):] for line in result.stdout.splitlines() if line.startswith(MARKER)]
    if result.returncode:
        output = (result.stdout + result.stderr).lower()
        if ('projektlokaler ansible-einstieg fehlt' in output or
                ('ssh_askpass' in output and any(marker in output for marker in ('no such file','not found','permission denied')))):
            raise ReadError('ssh-helper')
        if any(marker in output for marker in ('host key verification failed', 'remote host identification has changed')):
            raise ReadError('ssh-identity')
        if any(marker in output for marker in ('permission denied', 'authentication failed', 'incorrect ssh password')):
            raise ReadError('ssh-auth')
        raise ReadError('connection')
    if len(lines) != 1 or len(lines[0]) > 16384:
        raise ReadError('read-tools')
    try:
        data = json.loads(lines[0])
        if not isinstance(data, dict):
            raise ValueError
        if data.get('ok') is not True:
            # Codes select fixed local messages; never echo remote text or credentials.
            raise ReadError(data.get('code'))
        return validate_device(host, data['device'])
    except ReadError:
        raise
    except (ValueError, TypeError, KeyError):
        raise ReadError('device-data') from None


def validate_device(host, data):
    if (not isinstance(data, dict) or set(data) != {'host', 'address', 'adapters', 'protected'}
            or data['host'] != host or data['address'] != LAB_TARGETS[host]
            or not isinstance(data['adapters'], list) or not 2 <= len(data['adapters']) <= 8
            or not isinstance(data['protected'], list) or not 1 <= len(data['protected']) <= 32):
        raise ValueError('Geräteidentität stimmt nicht mit dem gewählten Client überein.')
    names, macs, keys = set(), set(), set()
    for row in data['adapters']:
        if (not isinstance(row, dict) or set(row) != {'interface', 'mac', 'uuid', 'usb', 'school'}
                or not isinstance(row['interface'], str) or not re.fullmatch(r'[a-zA-Z0-9_.-]{1,15}', row['interface'])
                or type(row['usb']) is not bool or type(row['school']) is not bool
                or not isinstance(row['mac'], str) or not re.fullmatch(r'(?:[0-9a-f]{2}:){5}[0-9a-f]{2}', row['mac'])):
            raise ValueError('Ungültige Adapterdaten.')
        if not isinstance(row['uuid'], str):
            raise ValueError('Ungültiges Netzwerkprofil.')
        uuid.UUID(row['uuid'])
        raw = bytes.fromhex(row['mac'].replace(':', ''))
        if not any(raw) or raw[0] & 1 or row['interface'] in names or row['mac'] in macs or row['uuid'] in keys:
            raise ValueError('Adapteridentität ist nicht eindeutig.')
        names.add(row['interface']); macs.add(row['mac']); keys.add(row['uuid'])
    school = [row for row in data['adapters'] if row['school']]
    if len(school) != 1 or school[0]['usb'] or not any(row['usb'] for row in data['adapters']):
        raise ValueError('Interne Schulkarte und USB-LAB-Adapter fehlen oder sind nicht eindeutig.')
    for network in data['protected']:
        if not isinstance(network, str):
            raise ValueError('Ungültiger geschützter Netzbereich.')
        ipaddress.ip_network(network, strict=True)
    return data


class Intake:
    def __init__(self, provision):
        self.provision = provision
        self.directory = provision.directory / 'intake'
        self.directory.mkdir(mode=0o700, exist_ok=True)
        for run in self.records():
            if run['status'] in {'running', 'queued'}:
                run.update(status='interrupted', error='APP wurde während der Geräteprüfung neu gestartet.')
                self.save(run)

    def records(self):
        return sorted((read_settings(path) for path in self.directory.glob('read-*.json')),
                      key=lambda item: item['started'], reverse=True)

    def save(self, run):
        path = self.directory / ('read-' + run['id'] + '.json')
        tmp = path.with_suffix('.tmp')
        tmp.unlink(missing_ok=True)
        write_private(tmp, json.dumps(run, ensure_ascii=False))
        tmp.replace(path)

    def state(self):
        fresh = self.provision.fresh_state()
        from maintenance.controller import targets
        enrolled = set(targets())
        ready = set(fresh['hosts'])
        hardware = {row['host'] for row in fresh['hardware']}
        latest = {}
        for run in self.provision.records():
            latest.setdefault(run['host'], run)
        clients = []
        for host in LAB_TARGETS:
            run = latest.get(host, {})
            status = ('managed' if host in enrolled else 'ready' if host in ready else
                      'hardware' if host in hardware else 'new')
            if run.get('status') in {'running', 'queued', 'failed', 'interrupted', 'stopped'}:
                status = run['status']
            clients.append(dict(host=host, address=LAB_TARGETS[host], status=status, managed=host in enrolled))
        return dict(clients=clients, reads=[{key: value for key, value in row.items() if key != 'pin'}
                                           for row in self.records()[:16]],
                    busy=fresh['busy'], pilot_accepted=self.pilot_accepted())

    def start(self, host, ssh_password, student, image_confirmed, replacement_fingerprint=''):
        if (not isinstance(host, str) or host not in LAB_TARGETS or host == 'LAB-08A'
                or image_confirmed is not True or not password_valid(ssh_password)
                or not isinstance(student, str) or (student and not re.fullmatch(r'[a-zA-Z0-9_.-]{1,64}', student))
                or not isinstance(replacement_fingerprint, str)):
            raise ValueError('Frisches Image bestätigen, Client und Administratorpasswort angeben.')
        if reserved(host) or (ADMISSIONS / (host + '.json')).exists():
            raise ValueError('Client ist bereits vorbereitet oder für Wartung reserviert.')
        if not self.provision.deployments.lock.acquire(blocking=False):
            return None
        try:
            pin, fingerprint = scan_key(LAB_TARGETS[host])
            known = ADMISSIONS / (host + '.active.known_hosts')
            old = known.read_text() if known.is_file() else next(
                (row['pin'] for row in self.records() if row['host'] == host and 'pin' in row), None)
            if old and old != pin and replacement_fingerprint != fingerprint:
                self.provision.deployments.lock.release()
                return dict(needs_key_confirmation=True, fingerprint=fingerprint, host=host)
            run = dict(id=uuid.uuid4().hex, host=host, status='running', started=self.provision.now(),
                       student=student, pin=pin, fingerprint=fingerprint)
            self.save(run)
            threading.Thread(target=self.run, args=(run, ssh_password), daemon=True).start()
            return {key: value for key, value in run.items() if key != 'pin'}
        except BaseException:
            self.provision.deployments.lock.release()
            raise

    def run(self, run, password, *, release_lock=True):
        try:
            run['device'] = read_device(run['host'], run['pin'], password)
            run['status'] = 'succeeded'
        except ReadError as error:
            run.update(status='failed', code=error.code, error=str(error))
        except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError):
            run.update(status='failed', code='failed', error=READ_ERRORS['failed'])
        finally:
            run['finished'] = self.provision.now()
            try:
                self.save(run)
            finally:
                if release_lock:
                    self.provision.deployments.lock.release()

    def start_many(self, hosts, ssh_password, image_confirmed, replacement_fingerprints):
        if (not isinstance(hosts, list) or not 1 <= len(hosts) <= 15
                or any(not isinstance(host, str) or host not in LAB_TARGETS or host == 'LAB-08A' for host in hosts)
                or len(set(hosts)) != len(hosts) or image_confirmed is not True
                or not password_valid(ssh_password) or not isinstance(replacement_fingerprints, dict)
                or any(host not in hosts or not isinstance(value, str)
                       for host, value in replacement_fingerprints.items())):
            raise ValueError('Frische Images bestätigen, Clients und gemeinsames Administratorpasswort angeben.')
        for host in hosts:
            if reserved(host) or (ADMISSIONS / (host + '.json')).exists():
                raise ValueError(host + ' ist bereits vorbereitet oder reserviert.')
        lock = self.provision.deployments.lock
        if not lock.acquire(blocking=False):
            return None
        try:
            runs = [dict(id=uuid.uuid4().hex, host=host, status='queued',
                         started=self.provision.now(), student='') for host in hosts]
            for run in runs:
                self.save(run)
            response = dict(runs=[dict(run) for run in runs])
            threading.Thread(target=self._read_batch, args=(runs, ssh_password, replacement_fingerprints),
                             daemon=True).start()
            return response
        except BaseException:
            lock.release()
            raise

    def _read_batch(self, runs, password, replacements):
        def read_one(run):
            try:
                host = run['host']
                run['status'] = 'running'
                self.save(run)
                pin, fingerprint = scan_key(LAB_TARGETS[host])
                known = ADMISSIONS / (host + '.active.known_hosts')
                old = known.read_text() if known.is_file() else next(
                    (row['pin'] for row in self.records() if row['host'] == host and 'pin' in row), None)
                if old and old != pin and replacements.get(host) != fingerprint:
                    # Never retain an unconfirmed pin as a new trusted identity.
                    run.update(status='key_changed', fingerprint=fingerprint,
                               error='SSH-Schlüssel geändert. Neuinstallation vor erneutem Prüfen bestätigen.',
                               finished=self.provision.now())
                    self.save(run)
                    return
                run.update(pin=pin, fingerprint=fingerprint)
                self.save(run)
                self.run(run, password, release_lock=False)
            except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError):
                run.update(status='failed', error='SSH nicht erreichbar oder Geräteprüfung fehlgeschlagen. Client und Schulnetz prüfen.',
                           finished=self.provision.now())
                self.save(run)
        try:
            from dashboard.provision import FRESH_WORKERS
            with ThreadPoolExecutor(max_workers=FRESH_WORKERS) as workers:
                list(workers.map(read_one, runs))
        finally:
            self.provision.deployments.lock.release()

    def setup_many(self, targets, ssh_password, sudo_password, confirmed, student):
        if (confirmed is not True or not isinstance(targets, list) or not 1 <= len(targets) <= 15
                or not password_valid(ssh_password) or not password_valid(sudo_password)
                or not isinstance(student, str) or not re.fullmatch(r'[a-zA-Z0-9_.-]{1,64}', student)
                or any(not isinstance(row, dict) or set(row) != {'run_id', 'lab_interface'}
                       or not isinstance(row['run_id'], str) or not re.fullmatch(r'[0-9a-f]{32}', row['run_id'])
                       or not isinstance(row['lab_interface'], str) for row in targets)
                or len({row['run_id'] for row in targets}) != len(targets)):
            raise ValueError('Geprüfte Clients, gemeinsames Passwort und IServ-Prüfkonto angeben.')
        lock = self.provision.deployments.lock
        if not lock.acquire(blocking=False):
            return None
        try:
            reads = [self.prepare(row['run_id'], row['lab_interface'], confirmed, student) for row in targets]
            if len({row['host'] for row in reads}) != len(reads):
                raise ValueError('Jeden Client nur einmal auswählen.')
            runs = [dict(id=uuid.uuid4().hex, host=row['host'], kind='bootstrap', status='queued',
                         started=self.provision.now()) for row in reads]
            for run in runs:
                self.provision.save(run)
            response = dict(runs=[dict(run) for run in runs])
            threading.Thread(target=self._setup_batch, args=(runs, reads, (ssh_password, sudo_password)),
                             daemon=True).start()
            return response
        except BaseException:
            lock.release()
            raise

    def _setup_batch(self, runs, reads, passwords):
        def setup_one(pair):
            run, read = pair
            try:
                host = run['host']
                if reserved(host) or (ADMISSIONS / (host + '.json')).exists():
                    raise ValueError('Client bereits vorbereitet oder reserviert.')
                data = self.provision.bootstrap.hardware(host)
                # Recheck the exact reviewed pin before any password reaches this host.
                pin = verified_key(LAB_TARGETS[host], read['fingerprint'])
                run['status'] = 'running'
                self.provision.save(run)
                self.provision.bootstrap.run(run, data, pin, passwords, self.provision.save,
                                             self.provision.now, release_lock=False)
            except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError):
                run.update(status='failed', finished=self.provision.now())
                (self.provision.directory / (run['id'] + '.log')).write_text(
                    'Zugang nicht eingerichtet. Erreichbarkeit, Geräteprüfung und SSH-Schlüssel erneut prüfen.\n')
                self.provision.save(run)
        try:
            from dashboard.provision import FRESH_WORKERS
            with ThreadPoolExecutor(max_workers=FRESH_WORKERS) as workers:
                list(workers.map(setup_one, zip(runs, reads)))
        finally:
            self.provision.deployments.lock.release()

    def prepare(self, run_id, lab_interface, confirmed, student=None):
        if confirmed is not True or not isinstance(run_id, str) or not re.fullmatch(r'[0-9a-f]{32}', run_id):
            raise ValueError('Geprüftes Gerät bestätigen.')
        run = read_settings(self.directory / ('read-' + run_id + '.json'))
        if run['status'] != 'succeeded' or (datetime.now().timestamp() - datetime.fromisoformat(run['finished']).timestamp()) > 1800:
            raise ValueError('Bitte das Gerät erneut prüfen; die Prüfung ist nicht mehr aktuell.')
        host = run['host']
        if next(row['id'] for row in self.records() if row['host'] == host) != run_id:
            raise ValueError('Eine neuere Geräteprüfung liegt vor.')
        student = run.get('student','') if student is None else student
        if not isinstance(student,str) or not re.fullmatch(r'[a-zA-Z0-9_.-]{1,64}',student):
            raise ValueError('Für die Baseline-Prüfung ein IServ-Schülerkonto aus cisco.lab angeben. Nur der Kontoname ist nötig.')
        device = validate_device(host, run['device'])
        school, = [row for row in device['adapters'] if row['school']]
        lab = next((row for row in device['adapters'] if row['usb'] and row['interface'] == lab_interface), None)
        if not lab:
            raise ValueError('Den gelesenen USB-LAB-Adapter auswählen.')
        identity = dict(school={key: school[key] for key in ('uuid', 'interface', 'mac', 'usb')},
                        lab={key: lab[key] for key in ('uuid', 'interface', 'mac', 'usb')},
                        protected_subnets=device['protected'])
        data = dict(lab_fresh_host=host, lab_fresh_confirmed=True, lab_fresh_student=student,
                    lab_fresh_packettracer_deb=PACKAGE,
                    lab_network_manifest=dict(version=1, host=host, group='cisco.lab', identity=identity))
        if host == 'LAB-08B':
            data['lab_fresh_rebuild_08b'] = True
        validate_hardware(host, data)
        if reserved(host) or (ADMISSIONS / (host + '.json')).exists():
            raise ValueError('Client ist bereits vorbereitet oder reserviert.')
        HARDWARE.mkdir(mode=0o700, parents=True, exist_ok=True)
        target = HARDWARE / (host + '.json')
        tmp = target.with_suffix('.intake')
        tmp.unlink(missing_ok=True)
        write_private(tmp, json.dumps(data))
        tmp.replace(target)
        run['student']=student;self.save(run)
        return run

    def setup(self, run_id, lab_interface, ssh_password, sudo_password, confirmed, student=None):
        # Saving hardware and starting bootstrap share the operation lock.
        if not password_valid(ssh_password) or not password_valid(sudo_password):
            raise ValueError('Gültiges Administratorpasswort eingeben.')
        if not self.provision.deployments.lock.acquire(blocking=False):
            return None
        try:
            run = self.prepare(run_id, lab_interface, confirmed, student)
        finally:
            self.provision.deployments.lock.release()
        return self.provision.bootstrap_start(run['host'], run['fingerprint'], ssh_password, sudo_password, confirmed)

    def pilot_accepted(self):
        path = self.directory / 'pilot-accepted.json'
        try:
            accepted = read_settings(path)
            run = next(row for row in self.provision.records() if row['id'] == accepted['run_id'])
            _, pin = self.provision.admission('LAB-08B', run['admission_id'])
            active = ADMISSIONS / 'LAB-08B.active.known_hosts'
            return (run['host'] == 'LAB-08B' and run['status'] == 'succeeded' and run.get('phase') == 'maintenance'
                    and pin.read_bytes() == active.read_bytes()
                    and hashlib.sha256(pin.read_bytes()).hexdigest() == run['pin_sha256'])
        except (OSError, ValueError, KeyError, StopIteration):
            return False

    def accept_pilot(self, run_id, confirmed):
        if confirmed is not True or not isinstance(run_id, str):
            raise ValueError('Den praktischen GUI-Test auf LAB-08B bestätigen.')
        run = next((row for row in self.provision.records() if row['id'] == run_id), {})
        latest = next((row for row in self.provision.records()
                       if row['host'] == 'LAB-08B' and row.get('kind') != 'bootstrap'), {})
        if (run.get('host') != 'LAB-08B' or run.get('status') != 'succeeded'
                or latest.get('id') != run_id
                or run.get('phase') != 'maintenance' or not run.get('pin_sha256')
                or not any(row['host'] == 'LAB-08B' and row['status'] == 'succeeded'
                           and row['started'] < run['started']
                           and hashlib.sha256(row['pin'].encode()).hexdigest() == run['pin_sha256']
                           for row in self.records())):
            raise ValueError('Zuerst LAB-08B vollständig über diesen GUI-Weg installieren.')
        if not self.provision.deployments.lock.acquire(blocking=False):
            return None
        try:
            path = self.directory / 'pilot-accepted.json'
            tmp = path.with_suffix('.tmp')
            tmp.unlink(missing_ok=True)
            write_private(tmp, json.dumps(dict(run_id=run_id, confirmed=self.provision.now())))
            tmp.replace(path)
            if not self.pilot_accepted():
                path.unlink(missing_ok=True)
                raise ValueError('Die aktuelle Clientidentität passt nicht zum abgeschlossenen B-Lauf.')
            return dict(accepted=True)
        finally:
            self.provision.deployments.lock.release()
