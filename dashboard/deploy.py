"""Allowlisted single-client Ansible jobs with local run records."""
from contextlib import ExitStack
from datetime import datetime
import hashlib
import json
from pathlib import Path
import shlex
import subprocess
from tempfile import TemporaryDirectory
import threading
import uuid

from dashboard.greeter_refresh import refresh_greeter
from maintenance.preview import BERLIN

ROOT = Path(__file__).resolve().parents[1]
LAB_TARGETS = {f'LAB-{number:02d}{side}': f'10.255.40.{2 * number - 1 + offset}'
               for number in range(1, 9) for offset, side in enumerate('AB')}
PILOTS = {name: LAB_TARGETS[name] for name in ('LAB-08A', 'LAB-08B')}
CISCO_PRIVATE = Path('local-private/cisco-account-vars.json')
CISCO_FILES = ('tasks/local-cisco.py', 'tasks/local-cisco-nss.c',
               'tasks/local-cisco-runtime.py',
               'tasks/cisco-wallpaper.yml', str(CISCO_PRIVATE))
IMAGE_CURRENT = 'LAB-Bildstand bereits vollständig installiert.'
GREETER_CURRENT = 'SDDM hat den aktuellen Bildstand bereits geladen.'
APPEARANCE_PROBE = 'dashboard/appearance-current.py'
APPEARANCE_FILES = (APPEARANCE_PROBE, 'roles/lab_desktop/files/snapshot.py',
                    'roles/lab_session/tasks/greeter-background.yml')
JOBS = {
    'firefox': ('Firefox-Richtlinien', 'lab_firefox', 'block04-firefox.yml'),
    'serial': ('PuTTY und Minicom', 'lab_serial', 'block02-serial.yml'),
    'session': ('KDE-Sitzung und Anmeldung', 'lab_session', 'block03-session.yml'),
    'appearance': ('Desktop- und Sperrbild', 'lab_appearance', 'dashboard/appearance-tasks.yml'),
    'cisco': ('Lokales Sammelkonto cisco', 'local_cisco', 'local-cisco.yml'),
}


CHECKS = {
    'cisco': {
        'login': 'cisco meldet sich lokal mit dem vereinbarten Kennwort an.',
        'desktop': 'Das LAB-Desktopbild ist bei der ersten cisco-Anmeldung sichtbar.',
        'network': 'USB-Labornetz ist konfigurierbar, das Schulprofil bleibt geschützt.',
        'console': 'USB-Konsole und LAB-Mitschnitt funktionieren.',
        'logout': 'Abmeldung und Rückkehr zur normalen Anmeldemaske funktionieren.',
    },
    'appearance': {
        'desktop': 'Das gewählte Desktopbild ist sichtbar.',
        'lock': 'Das gewählte Sperrbild ist sichtbar.',
        'greeter': 'Das gewählte Bild erscheint auch beim SDDM-Login.',
    },
    'firefox': {
        'homepage': 'Die Schulhomepage erscheint beim Start.',
        'new-tab': 'Strg+T öffnet einen leeren Tab.',
        'policies': 'about:policies meldet keine Fehler.',
    },
    'serial': {
        'minicom': 'Minicom öffnet die Cisco-Konsole als IServ-Schüler.',
        'putty': 'PuTTY öffnet die Cisco-Konsole als IServ-Schüler.',
        'exclusive': 'Ein zweiter Starter meldet den belegten Konsolenport.',
    },
    'session': {
        'login': 'IServ-Anmeldung und Start der Sitzung funktionieren.',
        'desktop': 'U-Zeiten startet, ohne alte Fenster wiederherzustellen.',
        'logout': 'KDE meldet sich nach Bestätigung sauber ab.',
    },
}


def catalog():
    return {'hosts': list(PILOTS), 'jobs': [
        {'id': key, 'label': value[0], 'checks': CHECKS[key]}
        for key, value in JOBS.items()
    ]}


def close_control_socket(socket, host):
    if socket.exists():
        try:
            subprocess.run(['/usr/bin/ssh', '-F', '/dev/null', '-S', str(socket),
                            '-O', 'exit', f'administrator@{LAB_TARGETS[host]}'],
                           timeout=4, capture_output=True)
        except (OSError, subprocess.SubprocessError):
            pass


class Deployments:
    def __init__(self, directory, known_hosts_b, appearance=None):
        self.directory = directory
        self.known_hosts_b = known_hosts_b
        self.appearance = appearance
        self.lock = threading.Lock()
        self.plans_lock = threading.Lock()
        self.review_lock = threading.Lock()
        self.plans = {}
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if directory.stat().st_mode & 0o077:
            raise ValueError('Laufverzeichnis muss privat sein.')
        for path in directory.glob('*.json'):
            run = json.loads(path.read_text())
            if run['status'] == 'running':
                run.update(status='interrupted', finished=self.now())
                self.save(run)

    @staticmethod
    def now():
        return datetime.now(BERLIN).isoformat(timespec='seconds')

    def command(self, host, job, *, release=False, rollout_images=None, control_socket=None):
        targets = LAB_TARGETS if release else PILOTS
        if host not in targets or job not in JOBS:
            raise ValueError('Nur freigegebene Aufträge auf LAB-08A oder LAB-08B.')
        known_hosts = self.known_hosts_b if host == 'LAB-08B' else Path.home() / '.ssh/known_hosts'
        key = Path.home() / '.ssh/id_ed25519'
        if not known_hosts or not known_hosts.is_file() or not key.is_file():
            raise ValueError('Verifizierte SSH-Hostdatei oder Verwaltungsschlüssel fehlt.')
        settings = {
            'dashboard_host': host,
            'dashboard_address': targets[host],
            'dashboard_role': JOBS[job][1],
            'ansible_user': 'administrator',
            'ansible_ssh_args': '-o ControlMaster=no -o ControlPath=none',
            'ansible_ssh_use_tty': False,
            'ansible_pipelining': True,
            'ansible_ssh_private_key_file': str(key),
            'ansible_ssh_common_args': ('-F /dev/null -o BatchMode=yes -o IdentitiesOnly=yes '
                                        '-o StrictHostKeyChecking=yes -o ConnectTimeout=8 '
                                        f'-o UserKnownHostsFile={shlex.quote(str(known_hosts))} '
                                        '-o GlobalKnownHostsFile=/dev/null'),
        }
        if release:
            settings['dashboard_pilot_addresses'] = {host: targets[host]}
        if control_socket is not None:
            settings['ansible_ssh_args'] = '-o ControlMaster=auto -o ControlPersist=30s'
            settings['ansible_control_path'] = str(control_socket)
            settings['ansible_control_path_dir'] = str(control_socket.parent)
        if job in ('appearance','session') and self.appearance:
            images = rollout_images if rollout_images is not None else self.appearance.active()
            settings.update(dashboard_desktop_image=str(images['desktop'][1]),
                            dashboard_lock_image=str(images['lock'][1]),
                            lab_greeter_image_source=str(images['lock'][1]))
            if job == 'appearance':
                for kind, (_, path) in images.items():
                    settings[f'dashboard_{kind}_sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
        command = ['/usr/bin/ansible-playbook', '-i', 'inventory.ini', '--limit', host]
        if job == 'cisco':
            secret = ROOT/CISCO_PRIVATE
            if secret.is_symlink() or not secret.is_file() or secret.stat().st_mode & 0o077:
                raise ValueError('Private Passwort-Hash-Datei für cisco fehlt oder ist nicht geschützt.')
            settings['lab_cisco_host'] = host
            return command + ['-e', '@'+str(CISCO_PRIVATE), '-e', json.dumps(settings), 'local-cisco.yml']
        return command + ['--tags', job, '-e', json.dumps(settings), 'dashboard-pilot.yml']

    def revision(self, job, *, root=ROOT, staged_images=None):
        _, role, entry = JOBS[job]
        paths = [root / name for name in ('dashboard-pilot.yml', 'inventory.ini', entry)]
        paths += sorted(path for path in (root / 'roles' / role).rglob('*')
                        if path.is_file() and '__pycache__' not in path.parts)
        if job == 'appearance':
            paths.extend(root/name for name in APPEARANCE_FILES)
        if job == 'cisco':
            paths.extend(root/name for name in CISCO_FILES)
        digest = hashlib.sha256()
        if job in ('appearance','session') and self.appearance:
            images = staged_images if staged_images is not None else self.appearance.active()
            for kind in ('desktop','lock') if job == 'appearance' else ('lock',):
                digest.update(hashlib.sha256(images[kind][1].read_bytes()).digest())
        for path in paths:
            digest.update(path.relative_to(root).as_posix().encode() + b'\0')
            digest.update(hashlib.sha256(path.read_bytes()).digest())
        return digest.hexdigest()

    def plan(self, host, job):
        command = self.command(host, job)
        revision = self.revision(job)
        hosts = subprocess.run(command + ['--list-hosts'], cwd=ROOT, capture_output=True,
                               text=True, check=True, timeout=20)
        lines = hosts.stdout.splitlines()
        if not any(line.strip() == 'hosts (1):' for line in lines) or [
            line.strip() for line in lines if line.strip() in PILOTS
        ] != [host]:
            raise ValueError('Inventar liefert kein eindeutiges Pilotziel.')
        result = subprocess.run(command + ['--list-tasks'], cwd=ROOT, capture_output=True,
                                text=True, check=True, timeout=20)
        tasks = [line.split('\tTAGS:', 1)[0].strip()
                 for line in result.stdout.splitlines()
                 if '\tTAGS:' in line and not line.lstrip().startswith('play #')]
        if self.revision(job) != revision:
            raise ValueError('Quellen geändert. Aufgabenliste erneut erzeugen.')
        token = uuid.uuid4().hex
        with self.plans_lock:
            self.plans[host, job] = (revision, token)
        result = dict(host=host, job=job, revision=revision, token=token, tasks=tasks)
        if job == 'appearance' and self.appearance:
            result['images'] = {kind: label or 'LAB-Standard' for kind, (label, _) in self.appearance.active().items()}
        return result

    def save(self, run):
        path = self.directory / (run['id'] + '.json')
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(run, ensure_ascii=False))
        temporary.replace(path)

    def history(self):
        runs = [json.loads(path.read_text()) for path in self.directory.glob('*.json')]
        return sorted(runs, key=lambda run: run['started'], reverse=True)[:30]

    @staticmethod
    def validate_id(run_id):
        if len(run_id) != 32 or any(char not in '0123456789abcdef' for char in run_id):
            raise FileNotFoundError

    def review(self, run_id, decision, checks, note):
        self.validate_id(run_id)
        if decision not in ('accepted', 'rejected') or len(note.encode()) > 300:
            raise ValueError('Ungültige Abnahme oder Anmerkung zu lang.')
        if not isinstance(checks, list) or not all(isinstance(check, str) for check in checks):
            raise ValueError('Ungültige Prüfliste.')
        with self.review_lock:
            run = json.loads((self.directory / (run_id + '.json')).read_text())
            expected = set(CHECKS[run['job']])
            selected = set(checks)
            if not selected <= expected or (decision == 'accepted' and selected != expected):
                raise ValueError('Für die Abnahme müssen alle Prüfpunkte erfüllt sein.')
            if decision == 'rejected' and not note.strip():
                raise ValueError('Bitte den Grund für die Ablehnung angeben.')
            if run['status'] != 'succeeded' or run.get('source_changed'):
                raise ValueError('Nur technisch erfolgreiche Läufe mit eindeutigem Quellenstand prüfen.')
            if run.get('review'):
                raise ValueError('Dieser Pilotdeploy wurde bereits bewertet.')
            run['review'] = dict(decision=decision, checks=sorted(selected), note=note.strip(),
                                 reviewed=self.now(), reviewer='lab-admin')
            self.save(run)
            return run

    def output(self, run_id):
        self.validate_id(run_id)
        path = self.directory / (run_id + '.log')
        with path.open('rb') as log:
            log.seek(max(0, path.stat().st_size - 65536))
            return log.read().decode(errors='replace')

    def start(self, host, job, revision, token):
        now = datetime.now(BERLIN)
        if host == 'LAB-08B' and now.weekday() < 5 and (17, 45) <= (now.hour, now.minute) < (20, 0):
            raise ValueError('LAB-08B ist für die automatische Wartung reserviert.')
        if not self.lock.acquire(blocking=False):
            return None
        try:
            self.command(host, job)
            with self.plans_lock:
                if self.plans.get((host, job)) != (revision, token):
                    raise ValueError('Zuerst die Aufgaben für dieses Pilotziel prüfen.')
                del self.plans[host, job]
            if revision != self.revision(job):
                raise ValueError('Quellen geändert. Aufgabenliste erneut erzeugen.')
            run = dict(id=uuid.uuid4().hex, host=host, job=job, revision=revision,
                       started=self.now(), status='running')
            (self.directory / (run['id'] + '.log')).touch(mode=0o600, exist_ok=False)
            self.save(run)
            threading.Thread(target=self.execute, args=(run,), daemon=True).start()
            return run.copy()
        except BaseException:
            self.lock.release()
            raise

    def execute(self, run):
        try:
            with (self.directory / (run['id'] + '.log')).open('a') as output:
                with ExitStack() as connections:
                    host = run['host']
                    directory = connections.enter_context(TemporaryDirectory(prefix='lab-pilot-ssh-'))
                    socket = Path(directory) / host
                    connections.callback(close_control_socket, socket, host)
                    command = self.command(host, run['job'], control_socket=socket)
                    result = subprocess.run(command, cwd=ROOT, stdin=subprocess.DEVNULL,
                                            stdout=output, stderr=subprocess.STDOUT)
                source_changed = run['revision'] != self.revision(run['job'])
                run.update(status='succeeded' if result.returncode == 0 else 'failed',
                           returncode=result.returncode, source_changed=source_changed)
                if source_changed:
                    output.write('Quellen während des Laufs geändert. Erneut als Pilot prüfen.\n')
                if result.returncode == 0 and not source_changed and run['job'] == 'appearance':
                    host = run['host']
                    known = self.known_hosts_b if host == 'LAB-08B' else Path.home()/'.ssh/known_hosts'
                    logged = Path(output.name).read_text(errors='replace')
                    run['greeter'] = (GREETER_CURRENT if GREETER_CURRENT in logged
                                      else refresh_greeter(host, PILOTS[host], known))
                    output.write(run['greeter'] + '\n')
        except (OSError, ValueError) as error:
            with (self.directory / (run['id'] + '.log')).open('a') as output:
                output.write(f'Ansible konnte nicht gestartet werden: {error}\n')
            run['status'] = 'failed'
        finally:
            try:
                if run['status'] == 'running':
                    run['status'] = 'failed'
                run['finished'] = self.now()
                self.save(run)
            finally:
                self.lock.release()
