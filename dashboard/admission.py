"""Prepare a single LAB client using reviewed hardware and a pinned SSH key."""
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import stat
import subprocess
import threading
import uuid

from dashboard.deploy import ADMISSIONS, LAB_TARGETS, ROOT
from dashboard.ansible_passwords import command as password_command
from maintenance.reservations import reserved
from network.security.lab_request import validate_identity

HARDWARE = ROOT / 'local-private/fresh-hardware'
APP_VEYON_KEY = ROOT / 'local-private/veyon-teacher-public.key'
VEYON_KEY = APP_VEYON_KEY if APP_VEYON_KEY.is_file() else Path('/etc/veyon/keys/public/teacher/key')
VEYON_SHA256 = 'b315bd556c3b9c944c89a11f9525d99873861724be86b0be488bb5e7f19b36ef'
FINGERPRINT = re.compile(r'SHA256:[A-Za-z0-9+/]{43}')


def veyon_available():
    try:
        return (not VEYON_KEY.is_symlink() and VEYON_KEY.is_file()
                and hashlib.sha256(VEYON_KEY.read_bytes()).hexdigest() == VEYON_SHA256)
    except OSError:
        return False


def read_settings(path, *, max_bytes=16384, label='Hardwaredatei'):
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), 'r') as file:
        info = os.fstat(file.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or info.st_mode & 0o077 or info.st_size > max_bytes):
            raise ValueError('Die '+label+' muss eine private lokale JSON-Datei sein.')
        data = json.load(file)
    if not isinstance(data, dict):
        raise ValueError('Ungültige '+label+'.')
    return data


def validate_hardware(host, data):
    try:
        manifest = data['lab_network_manifest']
        if (not isinstance(manifest, dict) or set(manifest) != {'version', 'host', 'group', 'identity'}
                or manifest['version'] != 1 or manifest['group'] != 'cisco.lab'
                or host != data['lab_fresh_host'] or host != manifest['host']
                or data.get('lab_fresh_confirmed') is not True
                or (host == 'LAB-08B' and data.get('lab_fresh_rebuild_08b') is not True)
                or not re.fullmatch(r'[a-zA-Z0-9_.-]+', data['lab_fresh_student'])
                or not Path(data['lab_fresh_packettracer_deb']).is_absolute()):
            raise ValueError
        validate_identity(manifest['identity'])
    except (KeyError, TypeError, ValueError, AttributeError) as error:
        raise ValueError('Host, Adapter, Testkonto und Paketpfad in der Hardwaredatei prüfen.') from error
    return manifest['identity']


def verified_key(address, fingerprint):
    result = subprocess.run(['/usr/bin/ssh-keyscan', '-T', '5', '-t', 'ed25519', address],
                            capture_output=True, text=True, timeout=8)
    keys = set()
    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) != 3 or fields[:2] != [address, 'ssh-ed25519']:
            continue
        try:
            blob = base64.b64decode(fields[2], validate=True)
        except ValueError:
            continue
        digest = base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip('=')
        if 'SHA256:' + digest == fingerprint:
            keys.add(' '.join(fields) + '\n')
    if result.returncode or len(keys) != 1:
        raise ValueError('Der SSH-Hostschlüssel stimmt nicht mit dem bestätigten Fingerabdruck überein.')
    return keys.pop()


def write_private(path, content):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(descriptor, 'w') as file:
            file.write(content)
    except BaseException:
        path.unlink(missing_ok=True)
        raise


class Bootstrap:
    def __init__(self, directory, operation_lock, controller_profile='teacher'):
        if controller_profile not in ('teacher', 'apps'):
            raise ValueError('Unbekanntes LAB-Steuerungsprofil.')
        self.directory = directory
        self.lock = operation_lock
        self.controller_ip = '203.0.113.200' if controller_profile == 'apps' else '203.0.113.100'

    @staticmethod
    def hardware(host):
        if host not in LAB_TARGETS:
            raise ValueError('Bitte einen LAB-Client wählen.')
        data = read_settings(HARDWARE / (host + '.json'))
        validate_hardware(host, data)
        return data

    def targets(self):
        available = []
        for host in LAB_TARGETS:
            try:
                data = self.hardware(host)
                identity = data['lab_network_manifest']['identity']
                if (ADMISSIONS / (host + '.json')).exists():
                    continue
                if reserved(host):
                    continue
                available.append(dict(host=host, school=identity['school']['mac'],
                                      lab=identity['lab']['mac']))
            except (OSError, ValueError):
                continue
        return available

    def start(self, host, fingerprint, ssh_password, sudo_password, confirmed, save, now):
        passwords = (ssh_password, sudo_password)
        if (confirmed is not True or not isinstance(fingerprint, str) or not FINGERPRINT.fullmatch(fingerprint)
                or any(not isinstance(value, str) or not 0 < len(value.encode()) <= 256
                       or value != value.strip() or any(char in value for char in '\r\n\0')
                       for value in passwords)):
            raise ValueError('Bestätigten ED25519-Fingerabdruck und gültige Einmalpasswörter eingeben.')
        if reserved(host):
            raise ValueError(host + ' ist für die Wartung reserviert.')
        if not self.lock.acquire(blocking=False):
            return None
        try:
            data = self.hardware(host)
            if (ADMISSIONS / (host + '.json')).exists():
                raise ValueError('Dieser Client ist bereits für die Basisinstallation vorbereitet.')
            pin = verified_key(LAB_TARGETS[host], fingerprint)
            run = dict(id=uuid.uuid4().hex, host=host, kind='bootstrap', status='running', started=now())
            save(run)
            threading.Thread(target=self.run, args=(run, data, pin, passwords, save, now), daemon=True).start()
            return dict(run)
        except BaseException:
            self.lock.release()
            raise

    def run(self, run, data, pin, passwords, save, now, *, release_lock=True):
        host, run_id = run['host'], run['id']
        hardware_path = HARDWARE / (host + '.json')
        pin_path = ADMISSIONS / (host + '.' + run_id + '.known_hosts')
        staged_pin = pin_path.with_suffix('.stage')
        pending = ADMISSIONS / (host + '.json')
        admitted = False
        published = False
        message = 'SSH-Erstaufnahme fehlgeschlagen. Lokales Administratorkonto, sudo und Hardwaredatei prüfen.'
        try:
            ADMISSIONS.mkdir(mode=0o700, parents=True, exist_ok=True)
            write_private(pin_path, pin)
            identity = data['lab_network_manifest']['identity']
            settings = dict(lab_bootstrap_host=host, ansible_host=LAB_TARGETS[host],
                            lab_bootstrap_controller_ip=self.controller_ip,
                            lab_bootstrap_school_mac=identity['school']['mac'],
                            lab_bootstrap_adapters={kind: identity[kind] for kind in ('school', 'lab')},
                            lab_bootstrap_identity_file=str(Path.home() / '.ssh/id_ed25519'),
                            lab_bootstrap_known_hosts=str(pin_path),
                            lab_bootstrap_rebuild_08b=data.get('lab_fresh_rebuild_08b') is True,
                            ansible_ssh_password_mechanism='ssh_askpass',
                            ansible_ssh_args='-o ControlMaster=no -o ControlPath=none',
                            ansible_ssh_common_args=(
                                '-F /dev/null -o StrictHostKeyChecking=yes -o GlobalKnownHostsFile=/dev/null '
                                f'-o UserKnownHostsFile={shlex.quote(str(pin_path))} -o HostKeyAlgorithms=ssh-ed25519 '
                                '-o ConnectTimeout=8 -o ServerAliveInterval=15 -o ServerAliveCountMax=3 '
                                '-o PubkeyAuthentication=no -o PreferredAuthentications=password,keyboard-interactive'))
            if self.run_ansible(host, settings, passwords):
                raise ValueError('SSH-Erstaufnahme fehlgeschlagen. Anmeldung, sudo und Adapterzuordnung prüfen.')
            write_private(staged_pin, pin)
            settings = dict(data, lab_fresh_known_hosts=str(pin_path))
            write_private(pending, json.dumps(settings, indent=2) + '\n')
            published = True
            staged_pin.replace(ADMISSIONS / (host + '.active.known_hosts'))
            admitted = True
            run['status'] = 'succeeded'
            message = 'Schlüsselzugang und sudo geprüft. Die Basisinstallation ist jetzt verfügbar.'
            try:
                if read_settings(hardware_path) == data:
                    hardware_path.rename(hardware_path.with_suffix('.' + run_id + '.used'))
            except (OSError, ValueError):
                message += ' Die Hardwaredatei konnte nicht archiviert werden.'
        except (OSError, ValueError, subprocess.SubprocessError):
            run['status'] = 'failed'
        finally:
            try:
                if not admitted:
                    if published:
                        pending.unlink(missing_ok=True)
                    staged_pin.unlink(missing_ok=True)
                    pin_path.unlink(missing_ok=True)
                with (self.directory / (run_id + '.log')).open('w') as log:
                    log.write(message + '\n')
                run['finished'] = now()
                save(run)
            finally:
                if release_lock:
                    self.lock.release()

    @staticmethod
    def run_ansible(host, settings, passwords):
        descriptors = []
        try:
            for password in passwords:
                fd = os.memfd_create('lab-bootstrap')
                descriptors.append(fd)
                os.fchmod(fd, 0o600)
                os.write(fd, password.encode())
            # Password files are anonymous Linux memory files, inherited only by Ansible.
            command = password_command('playbook', descriptors,
                                       ['-i', 'inventory.ini', '--limit', host,
                                        '-e', json.dumps(settings), 'bootstrap-client.yml'])
            env = dict(os.environ, ANSIBLE_LOG_PATH='', ANSIBLE_DEBUG='0', ANSIBLE_VERBOSITY='0')
            return subprocess.run(command, cwd=ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL, pass_fds=descriptors, env=env).returncode
        finally:
            for fd in descriptors:
                os.close(fd)
