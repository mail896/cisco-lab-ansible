"""Encrypted LAB snapshots, independent destinations and streamed ZIP exports."""
from contextlib import contextmanager
from collections import Counter
from datetime import datetime, timedelta
import hashlib
import json
import os
import platform
from pathlib import Path
import re
import secrets
import shlex
import shutil
import stat
import subprocess
import threading
from tempfile import TemporaryDirectory
import uuid
import zipfile

from maintenance.preview import BERLIN
from dashboard.runtime import ROOT

HOME = Path.home()
TARGET = 'backup@198.51.100.75:/Volumes/Repo/'
DATA_MOUNT = Path('/media/marc/data')
DATA_UUID = '001e0e07-067c-4549-97f0-3f4b0f499e12'
SOURCES = (
    HOME/'ansible-cisco-lab', HOME/'.ssh', HOME/'.veyon',
    HOME/'Bilder/CISCO-LAB',
    HOME/'.config/systemd/user',
    HOME/'backup-marc-labteacher.sh',
    HOME/'LAB-Bestandsaufnahme-2026-11-03/ssh-LAB-08B-frisches-image.known_hosts',
    HOME/'LAB-Bestandsaufnahme-2026-11-03/fresh-LAB-08B.json',
    Path('/etc/nginx/conf.d/cisco-lab-dashboard.conf'),
    Path('/etc/xdg/Veyon Solutions/Veyon.conf'), Path('/etc/veyon'),
    Path('/etc/hostname'), Path('/etc/hosts'), Path('/etc/fstab'),
)
CREDENTIAL = HOME/'.ssh/pw_macos-srv.cred'
SYSTEM_EXPORT = Path('/usr/local/sbin/cisco-lab-backup-system')
ARCHIVE_NAME = re.compile(r'LAB-Teacher-Konfiguration-\d{8}-\d{6}-[0-9a-f]{8}\.tar\.gz\.gpg')
APPS_ARCHIVE_NAME = re.compile(r'CISCO-LAB-APP-Konfiguration-\d{8}-\d{6}-[0-9a-f]{8}\.tar\.gz\.gpg')
APPS_REMOTE_HOST = 'backup@198.51.100.76'
APPS_REMOTE_DIRECTORY = '/Volumes/Repo/cisco-lab'
BACKUP_TARGETS = ('apps', 'mac', 'download')
DOWNLOAD_HOURS = 24
APPS_SSH_KEY = HOME / '.ssh/id_ed25519_backup'
APPS_KNOWN_HOSTS = HOME / '.ssh/mac.known_hosts'
APPS_SYSTEM_SOURCES = tuple(map(Path, (
    '/etc/systemd/system/cisco-lab-dashboard.service',
    '/etc/systemd/system/cisco-lab-controller.service',
    '/etc/systemd/system/cisco-lab-controller.timer',
    '/etc/apache2/cisco-lab.conf',
)))


def apps_system_sources():
    for path in APPS_SYSTEM_SOURCES:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise ValueError('Die eigene LAB-Systemkonfiguration ist nicht eindeutig rootgeschützt.')
    return APPS_SYSTEM_SOURCES


class Backups:
    def __init__(self, directory, deploys, state_dir, profile='teacher'):
        if profile not in ('teacher', 'apps'):
            raise ValueError('Unbekanntes Sicherungsprofil.')
        self.profile = profile
        self.archive_name = APPS_ARCHIVE_NAME if profile == 'apps' else ARCHIVE_NAME
        self.directory = directory
        self.deploys = deploys
        self.state_dir = state_dir
        self.key = directory/'recovery-key'
        self.download_slots = threading.BoundedSemaphore(2)
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.ensure_key()
        for run in self.history(limit=None):
            if run['status'] == 'running':
                run['status'] = 'interrupted'
                self.save(run)
        cleanup = self.cleanup_state()
        if cleanup and cleanup['status'] == 'running':
            cleanup['status'] = 'interrupted'
            self.save_cleanup(cleanup)

    def ensure_key(self):
        if not self.key.exists():
            with self.key.open('x') as file:
                file.write(secrets.token_urlsafe(48)+'\n')
            self.key.chmod(0o600)
        self.check_private(self.key)

    @staticmethod
    def check_private(path):
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_uid != HOME.stat().st_uid:
            raise ValueError(f'Private Datei fehlt oder Rechte prüfen: {path.name}')

    def transfer_ready(self):
        if self.profile == 'teacher':
            return True
        try:
            self.check_private(APPS_SSH_KEY)
            self.check_private(APPS_KNOWN_HOSTS)
            return bool(APPS_KNOWN_HOSTS.read_bytes())
        except (OSError, ValueError):
            return False

    @staticmethod
    def apps_ssh():
        return ['/usr/bin/ssh', '-T', '-F', '/dev/null', '-i', str(APPS_SSH_KEY),
                '-o', 'BatchMode=yes', '-o', 'IdentitiesOnly=yes',
                '-o', 'StrictHostKeyChecking=yes',
                '-o', f'UserKnownHostsFile={APPS_KNOWN_HOSTS}',
                '-o', 'GlobalKnownHostsFile=/dev/null', '-o', 'ConnectTimeout=8',
                APPS_REMOTE_HOST]

    def apps_request(self, command, *, source=None, timeout=30):
        return subprocess.run(self.apps_ssh() + [command], stdin=source or subprocess.DEVNULL,
                              capture_output=True, text=True, check=True, timeout=timeout)

    def apps_target_ready(self):
        if self.apps_request('check').stdout.strip() != 'ready':
            raise ValueError('Der eingeschränkte Mac-Backupempfänger ist nicht bereit.')

    def apps_upload(self, archive, checksum):
        name = archive.name
        if not APPS_ARCHIVE_NAME.fullmatch(name) or not re.fullmatch('[a-f0-9]{64}', checksum):
            raise ValueError('Ungültige APP-Archivmetadaten.')
        if archive.is_symlink() or not archive.is_file():
            raise ValueError('APP-Archiv für externe Kopie fehlt.')
        with archive.open('rb') as source:
            response = self.apps_request(f'put {name} {checksum}', source=source, timeout=900)
        if response.stdout.strip() != f'SHA256:{checksum}':
            raise ValueError('Externe APP-Sicherung stimmt nicht mit der lokalen Prüfsumme überein.')

    def save(self, run):
        path = self.directory/(run['id']+'.json')
        temp = path.with_suffix('.tmp')
        temp.write_text(json.dumps(run, ensure_ascii=False))
        temp.replace(path)

    def history(self, limit=20):
        runs = [json.loads(path.read_text()) for path in self.directory.glob('*.json')]
        return sorted(runs,key=lambda run: run['started'],reverse=True)[:limit]

    @staticmethod
    def targets(run):
        return run.get('targets', ['apps'] + (['mac'] if run.get('transfer') or run.get('copied') else []))

    @staticmethod
    def has_copy(run, target):
        return bool(run.get('data_copy' if target == 'apps' else 'copied')
                    and not run.get('pruned_at') and not run.get(target+'_removed_at'))

    @classmethod
    def protected_ids(cls, runs):
        available = [run for run in runs if not run.get('pruned_at') and run.get('file')]
        protected = set()
        for target, field in (('apps','local_saved_at'),('mac','copied_at')):
            copies = [run for run in available if cls.has_copy(run,target)]
            if copies:
                protected.add(max(copies,key=lambda run:run.get(field,run['started']))['id'])
        confirmed = [run for run in available if run.get('confirmed_good_at')]
        if confirmed:
            protected.add(max(confirmed,key=lambda run:run['confirmed_good_at'])['id'])
        return protected

    def local_directory(self):
        return HOME/'backup_data' if self.profile == 'apps' else DATA_MOUNT/'backup_data'

    def download_directory(self):
        destination = self.directory/'downloads'
        destination.mkdir(mode=0o700, exist_ok=True)
        info = destination.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != HOME.stat().st_uid or info.st_mode & 0o077:
            raise ValueError('Privater Downloadbereich ist nicht verfügbar.')
        return destination

    def download_path(self, run):
        name = run.get('file', '')
        if not self.archive_name.fullmatch(name) or not re.fullmatch('[a-f0-9]{64}', run.get('sha256', '')):
            raise ValueError('Ungültige Sicherungsdatei.')
        if self.has_copy(run,'apps'):
            return self.local_directory()/name
        expires = run.get('download_expires_at')
        if run.get('download_cache') and expires and datetime.fromisoformat(expires) > datetime.now(BERLIN):
            return self.directory/'downloads'/name
        raise ValueError('Für diese Sicherung ist kein Download mehr verfügbar.')

    def download_available(self, run):
        if run.get('status') not in ('finished', 'failed', 'interrupted'):
            return False
        try:
            info = self.download_path(run).lstat()
            return stat.S_ISREG(info.st_mode) and info.st_uid == HOME.stat().st_uid and not info.st_mode & 0o077
        except (OSError, ValueError):
            return False

    def state(self, limit=20):
        runs = [dict(run, targets=self.targets(run), download_available=self.download_available(run)) for run in self.history(limit=None)]
        locations = {}
        for target in BACKUP_TARGETS:
            candidates = [run for run in runs if (self.has_copy(run,target) if target != 'download' else run.get('download_ready_at'))]
            field = {'apps': 'local_saved_at', 'mac': 'copied_at', 'download': 'download_ready_at'}[target]
            last = max(candidates, key=lambda run: run.get(field, run['started']), default=None)
            locations[target] = dict(path=str(self.local_directory()) if target == 'apps' else
                APPS_REMOTE_DIRECTORY if self.profile == 'apps' and target == 'mac' else '/Volumes/Repo' if target == 'mac' else 'Downloads im Browser',
                last=dict(id=last['id'],time=last.get(field,last['started']),file=last.get('file')) if last else None)
        cleanup = self.cleanup_state()
        return dict(runs=runs[:limit], latest=runs[0] if runs else None, locations=locations, cleanup=cleanup,
                    mode=self.profile, transfer_available=self.transfer_ready(),
                    busy=any(run['status'] == 'running' for run in runs) or bool(cleanup and cleanup['status'] == 'running'))

    def cleanup_state(self):
        path = self.directory/'cleanup-state'
        if not path.exists():
            return None
        self.check_private(path)
        return json.loads(path.read_text())

    def save_cleanup(self, job):
        path = self.directory/'cleanup-state'
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(job,ensure_ascii=False))
        temporary.chmod(0o600)
        temporary.replace(path)

    @staticmethod
    def check_cleanup_file(path):
        info = path.lstat()
        # Early encrypted archives used 0644 inside the same private 0700
        # directory. Read bits do not make deletion ambiguous; foreign writes do.
        if not stat.S_ISREG(info.st_mode) or info.st_uid != HOME.stat().st_uid or info.st_mode & 0o022:
            raise ValueError('Sicherungsdatei ist nicht eindeutig dem LAB zugeordnet.')

    def cleanup_plan(self):
        if self.profile != 'apps':
            raise ValueError('Diese Bereinigung ist nur für die APP-Ablage eingerichtet.')
        runs = self.history(limit=None)
        protected = self.protected_ids(runs)
        protected_files = {run.get('file') for run in runs if run['id'] in protected}
        names = Counter(run.get('file') for run in runs)
        candidates, skipped = [], []
        for run in runs:
            name = run.get('file','')
            if run['id'] in protected or run.get('pruned_at') or run.get('status') in ('running','queued') or not name:
                continue
            if (not self.archive_name.fullmatch(name) or name in protected_files or names[name] != 1
                    or not re.fullmatch('[a-f0-9]{64}',run.get('sha256',''))):
                skipped.append(run['id']);continue
            local, remote = self.has_copy(run,'apps'), self.has_copy(run,'mac')
            if not local and not remote:
                continue
            size = run.get('size',0)
            if type(size) is not int or size < 0:
                skipped.append(run['id']);continue
            signature = []
            try:
                self.deploys.validate_id(run['id'])
                if local:
                    folder = self.local_directory()
                    info = folder.lstat()
                    if not stat.S_ISDIR(info.st_mode) or info.st_uid != HOME.stat().st_uid or info.st_mode & 0o077:
                        raise ValueError('Unsichere Ablage.')
                    archive, digest = folder/name, folder/(name+'.sha256')
                    self.check_cleanup_file(archive);self.check_cleanup_file(digest)
                    info = archive.stat()
                    if ((run.get('size') is not None and info.st_size != run['size'])
                            or digest.stat().st_size > 256 or digest.read_text() != f"{run['sha256']}  {name}\n"):
                        raise ValueError('Archivmetadaten weichen ab.')
                    size = info.st_size
                    signature = [info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns]
                candidates.append(dict(id=run['id'],file=name,started=run['started'],size=size,
                                       apps=local,mac=remote,sha256=run['sha256'],signature=signature))
            except (OSError,ValueError):
                skipped.append(run['id'])
        payload = dict(candidates=candidates,protected=sorted(protected),skipped=skipped)
        payload['token'] = hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()
        payload['bytes'] = {target:sum(row['size'] for row in candidates if row[target]) for target in ('apps','mac')}
        return payload

    def cleanup(self, token, confirmed):
        if confirmed is not True or not isinstance(token,str) or not re.fullmatch('[a-f0-9]{64}',token):
            raise ValueError('Die angezeigte Bereinigung muss bestätigt werden.')
        if not self.deploys.lock.acquire(blocking=False):
            return None
        try:
            plan = self.cleanup_plan()
            if not secrets.compare_digest(token,plan['token']):
                raise ValueError('Die Sicherungsliste hat sich geändert. Bitte die Vorschau erneut öffnen.')
            if not plan['candidates']:
                raise ValueError('Keine älteren eindeutig zugeordneten Archive zum Bereinigen vorhanden.')
            job = dict(id=uuid.uuid4().hex,status='running',started=datetime.now(BERLIN).isoformat(timespec='seconds'),
                       total=len(plan['candidates']),processed=0,removed=0,bytes=dict(apps=0,mac=0),errors=[])
            self.save_cleanup(job)
            threading.Thread(target=self.execute_cleanup,args=(job,plan['candidates']),daemon=True).start()
            return dict(job)
        except BaseException:
            self.deploys.lock.release()
            raise

    def execute_cleanup(self, job, candidates):
        try:
            for row in candidates:
                run = json.loads((self.directory/(row['id']+'.json')).read_text())
                try:
                    stamp = datetime.now(BERLIN).isoformat(timespec='seconds')
                    if row['apps']:
                        archive = self.local_directory()/row['file']
                        self.check_cleanup_file(archive);self.check_cleanup_file(archive.with_suffix('.gpg.sha256'))
                        info = archive.stat()
                        if [info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns] != row['signature']:
                            raise ValueError('Lokales Archiv wurde verändert.')
                    if row['mac']:
                        result = self.prune_remote_apps(row['file'],row['sha256'])
                        run['mac_removed_at'] = stamp
                        if result != 'absent':
                            job['bytes']['mac'] += row['size']
                        self.save(run)
                    if row['apps']:
                        archive = self.local_directory()/row['file']
                        self.check_cleanup_file(archive);self.check_cleanup_file(archive.with_suffix('.gpg.sha256'))
                        info = archive.stat()
                        if [info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns] != row['signature']:
                            raise ValueError('Lokales Archiv wurde verändert.')
                        archive.unlink()
                        run['apps_removed_at'] = stamp
                        job['bytes']['apps'] += row['size']
                        self.save(run)
                        archive.with_suffix('.gpg.sha256').unlink()
                    if not self.has_copy(run,'apps') and not self.has_copy(run,'mac'):
                        run.update(pruned_at=stamp,pruned_reason='manual_cleanup')
                    self.save(run)
                    job['removed'] += 1
                except (OSError,ValueError,subprocess.SubprocessError):
                    job['errors'].append(dict(id=row['id'],file=row['file'],error='Archiv nicht vollständig bereinigt. Verbliebene Kopien erhalten.'))
                    if not self.has_copy(run,'apps') and not self.has_copy(run,'mac'):
                        run.update(pruned_at=stamp,pruned_reason='manual_cleanup')
                    self.save(run)
                job['processed'] += 1
                self.save_cleanup(job)
            job['status'] = 'partial' if job['errors'] and job['removed'] else 'failed' if job['errors'] else 'finished'
        finally:
            try:
                if job['status'] == 'running':
                    job['status'] = 'interrupted'
                job['finished'] = datetime.now(BERLIN).isoformat(timespec='seconds')
                self.save_cleanup(job)
            finally:
                self.deploys.lock.release()

    def clean_downloads(self):
        # Only expired, explicitly temporary LAB downloads; never the APP archives.
        for run in self.history(limit=None):
            expires = run.get('download_expires_at')
            if not run.get('download_cache') or not expires or datetime.fromisoformat(expires) > datetime.now(BERLIN):
                continue
            name = run.get('file', '')
            if not self.archive_name.fullmatch(name):
                continue
            for path in (self.directory/'downloads'/name, self.directory/'downloads'/(name+'.sha256')):
                if path.is_file() and not path.is_symlink():
                    path.unlink()

    @contextmanager
    def download(self, run_id):
        self.deploys.validate_id(run_id)
        if not self.download_slots.acquire(blocking=False):
            raise ValueError('Zwei Downloads laufen bereits. Bitte anschließend erneut versuchen.')
        try:
            run = json.loads((self.directory/(run_id+'.json')).read_text())
            if run.get('status') not in ('finished', 'failed', 'interrupted'):
                raise ValueError('Die Sicherung ist noch nicht abgeschlossen.')
            path = self.download_path(run)
            with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), 'rb') as source:
                info = os.fstat(source.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_uid != HOME.stat().st_uid or info.st_mode & 0o077:
                    raise ValueError('Die Sicherungsdatei ist nicht eindeutig privat.')
                if run.get('size') is not None and info.st_size != run['size']:
                    raise ValueError('Die Sicherungsdatei hat eine abweichende Größe.')
                yield run, source
        finally:
            self.download_slots.release()

    @staticmethod
    def write_zip(run, source, output):
        # The encrypted member is already compressed. Stream it once without a
        # second large file or an archive-sized RAM buffer, also on socket output.
        with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_STORED, allowZip64=True) as saved:
            info = zipfile.ZipInfo(run['file'])
            info.external_attr = 0o600 << 16
            with saved.open(info, 'w', force_zip64=True) as member:
                shutil.copyfileobj(source, member, length=1024*1024)
            saved.writestr(run['file']+'.sha256', f"{run['sha256']}  {run['file']}\n")
            saved.writestr('RESTORE.txt', (ROOT/'deployment/apps/RESTORE.txt').read_bytes())

    def confirm(self, run_id):
        self.deploys.validate_id(run_id)
        if not self.deploys.lock.acquire(blocking=False):
            raise ValueError('Ein Verwaltungsauftrag läuft. Bestätigung anschließend erneut versuchen.')
        try:
            path = self.directory/(run_id+'.json')
            run = json.loads(path.read_text())
            if run['status'] != 'finished' or not run.get('file') or run.get('pruned_at'):
                raise ValueError('Nur eine vorhandene erfolgreiche Sicherung kann bestätigt werden.')
            if not self.has_copy(run,'apps') or (self.profile == 'apps' and (not self.has_copy(run,'mac') or not run.get('remote_restore_verified_at'))):
                raise ValueError('APP-Sicherung benötigt einen Restore-Test aus der Mac-Kopie.')
            archive = self.archive_directory()/run['file']
            if not self.archive_name.fullmatch(run['file']) or archive.is_symlink() or not archive.is_file():
                raise ValueError('Sicherungsdatei fehlt oder ist nicht eindeutig.')
            with archive.open('rb') as file:
                if hashlib.file_digest(file, 'sha256').hexdigest() != run.get('sha256'):
                    raise ValueError('Sicherungsdatei stimmt nicht mit der Prüfsumme überein.')
            run['confirmed_good_at'] = datetime.now(BERLIN).isoformat(timespec='seconds')
            self.save(run)
            return run
        finally:
            self.deploys.lock.release()

    def prune(self, log):
        runs = [json.loads(path.read_text()) for path in self.directory.glob('*.json')]
        successful = [run for run in runs if run.get('status') == 'finished' and not run.get('pruned_at')]
        if not successful:
            return
        protected = self.protected_ids(successful)
        if not protected:
            protected.add(max(successful,key=lambda run:run['started'])['id'])
        cutoff = datetime.now(BERLIN) - timedelta(days=90)
        destination = self.archive_directory()
        for run in successful:
            if run['id'] in protected or datetime.fromisoformat(run['started']) >= cutoff:
                continue
            name = run.get('file', '')
            if not self.archive_name.fullmatch(name):
                log.write('Aufbewahrung übersprungen. Unbekannter Archivname.\n')
                continue
            archive = destination/name
            checksum = archive.with_suffix('.gpg.sha256')
            local = self.has_copy(run,'apps')
            if local and any(path.is_symlink() or not path.is_file() for path in (archive, checksum)):
                log.write(f'Aufbewahrung übersprungen. Archivpaar fehlt: {name}\n')
                continue
            if local:
                with archive.open('rb') as file:
                    if hashlib.file_digest(file, 'sha256').hexdigest() != run.get('sha256'):
                        log.write(f'Aufbewahrung übersprungen. Prüfsumme abweichend: {name}\n')
                        continue
            try:
                if self.has_copy(run,'mac'):
                    if self.profile == 'apps':
                        self.prune_remote_apps(name, run['sha256'])
                    else:
                        self.prune_remote(name, run['sha256'], log)
                if local:
                    archive.unlink()
                    checksum.unlink()
                run['pruned_at'] = datetime.now(BERLIN).isoformat(timespec='seconds')
                self.save(run)
                log.write(f'Nach 90 Tagen entfernt: {name}\n')
            except (OSError, subprocess.SubprocessError):
                log.write(f'Aufbewahrung zur Prüfung ausgesetzt: {name}\n')

    @staticmethod
    def prune_remote(name, checksum, log):
        if not ARCHIVE_NAME.fullmatch(name) or not re.fullmatch('[a-f0-9]{64}', checksum):
            raise ValueError('Ungültige Archivmetadaten.')
        # Only this known archive pair is eligible. Other files in Repo are untouched.
        script = ('set -eu\ncd /Volumes/Repo\n'
                  f'name={shlex.quote(name)}\nexpected={shlex.quote(checksum)}\n'
                  'if [ ! -e "$name" ] && [ ! -e "$name.sha256" ]; then exit 0; fi\n'
                  'test -f "$name" && test ! -L "$name"\n'
                  'test -f "$name.sha256" && test ! -L "$name.sha256"\n'
                  'actual=$(/usr/bin/shasum -a 256 "$name")\n'
                  'test "${actual%% *}" = "$expected"\n'
                  '/bin/rm "$name" "$name.sha256"\n')
        subprocess.run(['/usr/bin/sshpass', '-f', str(CREDENTIAL), '/usr/bin/ssh', '-F', '/dev/null',
                        '-o', 'StrictHostKeyChecking=yes', '-o', 'PubkeyAuthentication=no',
                        '-o', 'PreferredAuthentications=password,keyboard-interactive',
                        '-o', 'NumberOfPasswordPrompts=1', '-o', 'ConnectTimeout=8',
                        'backup@198.51.100.75', '/bin/sh'], input=script, text=True,
                       stdout=log, stderr=log, check=True, timeout=120)

    def prune_remote_apps(self, name, checksum):
        if not APPS_ARCHIVE_NAME.fullmatch(name) or not re.fullmatch('[a-f0-9]{64}', checksum):
            raise ValueError('Ungültige APP-Archivmetadaten.')
        response = self.apps_request(f'delete {name} {checksum}', timeout=120)
        if response.stdout.strip() not in ('deleted', 'absent'):
            raise ValueError('Externe APP-Aufbewahrung wurde nicht bestätigt.')
        return response.stdout.strip()

    def archive_directory(self):
        if self.profile == 'apps':
            destination = HOME / 'backup_data'
            destination.mkdir(mode=0o700, exist_ok=True)
            info = destination.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != HOME.stat().st_uid or info.st_mode & 0o077:
                raise ValueError('Privates APP-Sicherungsziel ist nicht verfügbar.')
            return destination
        destination = DATA_MOUNT / 'backup_data'
        if destination.is_symlink() or not destination.is_dir():
            raise ValueError('Ziel backup_data auf der data-Partition ist nicht verfügbar.')
        mount = subprocess.run(
            ['/usr/bin/findmnt', '--noheadings', '--output', 'UUID',
             '--mountpoint', str(DATA_MOUNT)],
            capture_output=True, text=True, timeout=8,
        )
        if mount.returncode or mount.stdout.strip() != DATA_UUID:
            raise ValueError('Die erwartete data-Partition ist nicht eingehängt.')
        return destination

    def start(self, transfer=None, *, targets=None):
        if targets is None:
            if type(transfer) is not bool:
                raise ValueError('Ungültige Auswahl für SCP.')
            targets = ['apps'] + (['mac'] if transfer else [])
        elif transfer is not None or not isinstance(targets, list) or not targets or len(targets) > 3 or any(
                not isinstance(target,str) or target not in BACKUP_TARGETS for target in targets) or len(set(targets)) != len(targets):
            raise ValueError('Bitte mindestens ein gültiges Sicherungsziel auswählen.')
        targets = [target for target in BACKUP_TARGETS if target in targets]
        transfer = 'mac' in targets
        if self.profile == 'apps' and transfer and not self.transfer_ready():
            raise ValueError('Externe APP-Sicherung ist noch nicht eingerichtet.')
        if not self.deploys.lock.acquire(blocking=False):
            return None
        try:
            self.ensure_key()
            self.clean_downloads()
            if 'apps' in targets:
                self.archive_directory()
            if transfer and self.profile == 'teacher':
                self.check_private(CREDENTIAL)
            run = dict(id=uuid.uuid4().hex, started=datetime.now(BERLIN).isoformat(timespec='seconds'),
                       status='running', transfer=transfer, targets=targets)
            self.save(run)
            threading.Thread(target=self.execute,args=(run,),daemon=True).start()
            return dict(run)
        except BaseException:
            self.deploys.lock.release()
            raise

    def execute(self, run):
        prefix = 'CISCO-LAB-APP-Konfiguration-' if self.profile == 'apps' else 'LAB-Teacher-Konfiguration-'
        stem = prefix + datetime.now(BERLIN).strftime('%Y%m%d-%H%M%S') + '-' + run['id'][:8]
        partial = None
        system_directory = None
        transfer_directory = None
        restore_directory = None
        try:
            targets = self.targets(run)
            if 'apps' in targets:
                destination = self.archive_directory()
            elif 'download' in targets:
                destination = self.download_directory()
            else:
                transfer_directory = TemporaryDirectory(prefix='transfer-', dir=self.directory)
                destination = Path(transfer_directory.name)
            archive = destination/(stem+'.tar.gz.gpg')
            partial = archive.with_suffix('.partial')
            with (self.directory/(run['id']+'.log')).open('w') as log:
                system_archive = None
                if self.profile == 'teacher' and (SYSTEM_EXPORT.exists() or SYSTEM_EXPORT.is_symlink()):
                    info = SYSTEM_EXPORT.lstat()
                    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
                        raise ValueError('Systemexport ist nicht rootgeschützt.')
                    system_directory = TemporaryDirectory(prefix='system-export-', dir=self.directory)
                    system_archive = Path(system_directory.name)/'teacher-system.tar.gz'
                    with system_archive.open('wb') as output:
                        subprocess.run(['/usr/bin/sudo', '-n', str(SYSTEM_EXPORT)], stdout=output,
                                       stderr=log, check=True, timeout=90)
                    system_archive.chmod(0o600)
                    run['root_system_copy'] = True
                    run['root_system_member'] = str(system_archive.relative_to('/'))
                source_paths = (ROOT, HOME/'.ssh', HOME/'private', HOME/'Bilder/CISCO-LAB') if self.profile == 'apps' else SOURCES
                if self.profile == 'apps':
                    source_paths += apps_system_sources()
                    run['lab_system_copy'] = True
                paths = source_paths + tuple(self.state_dir/name for name in (
                    'passwort', 'auth', 'deploys', 'room', 'appearance', 'appearance-releases', 'provision', 'maintenance', 'teacher', 'playbook-groups', 'exercises',
                ))
                # Keep retention/confirmation records without copying the recovery key.
                paths += tuple(self.directory.glob('*.json')) + tuple(self.directory.glob('*.log'))
                paths += (self.directory/'cleanup-state',)
                paths += tuple(self.state_dir/'software'/name for name in ('profiles','runs','packages'))
                if system_archive is not None:
                    paths += (system_archive,)
                if self.profile == 'apps':
                    restore_directory = TemporaryDirectory(prefix='restore-info-', dir=self.directory)
                    manifest = Path(restore_directory.name)/'restore-manifest.json'
                    manifest.write_text(json.dumps(dict(format=1, created=run['started'], run_id=run['id'],
                        targets=targets, operating_system=platform.freedesktop_os_release(), architecture=platform.machine(),
                        python=platform.python_version(), repository='https://github.com/mail896/cisco-lab-ansible',
                        code='/opt/cisco-lab', home=str(HOME), state=str(self.state_dir), account='cisco-lab',
                        system_files=list(map(str, APPS_SYSTEM_SOURCES)),
                        prerequisites=['python3','python3-venv','python3-pip','openssh-client','gnupg','tar','apache2','ca-certificates','git'],
                        generated_excluded=['/opt/cisco-lab/.venv','/opt/cisco-lab/collections'],
                        separately_required=['recovery-key','HTTPS certificate and key','network and DNS configuration'],
                        guide='/opt/cisco-lab/deployment/apps/RESTORE.txt'), ensure_ascii=False,indent=2)+'\n')
                    manifest.chmod(0o600)
                    run['restore_manifest_member'] = str(manifest.relative_to('/'))
                    self.save(run)
                    paths += (manifest,)
                paths = [str(path.relative_to('/')) for path in paths if path.exists()]
                tar = ['/usr/bin/tar','--acls','--xattrs','-czf','-','-C','/',
                       '--exclude=__pycache__','--exclude=*.pyc',
                       '--exclude=opt/cisco-lab/.venv','--exclude=opt/cisco-lab/collections',
                       f'--exclude={ROOT.relative_to("/")}/local-private',
                       '--exclude=home/marc/.ssh/pw_macos-srv.cred','--'] + paths
                with subprocess.Popen(tar, stdout=subprocess.PIPE, stderr=log) as source:
                    try:
                        encrypt = ['/usr/bin/gpg','--batch','--no-tty','--no-symkey-cache',
                                   '--pinentry-mode','loopback','--passphrase-file',str(self.key),
                                   '--symmetric','--cipher-algo','AES256','--compress-algo','none',
                                   '--output',str(partial)]
                        if self.profile == 'apps':
                            gpg_home = self.directory / '.gnupg'
                            gpg_home.mkdir(mode=0o700, exist_ok=True)
                            info = gpg_home.lstat()
                            if not stat.S_ISDIR(info.st_mode) or info.st_uid != HOME.stat().st_uid or info.st_mode & 0o077:
                                raise ValueError('Privates APP-GPG-Verzeichnis ist nicht verfügbar.')
                            encrypt[1:1] = ['--homedir', str(gpg_home)]
                        subprocess.run(encrypt,stdin=source.stdout,stdout=log,stderr=log,
                                       check=True,timeout=900)
                    finally:
                        source.stdout.close()
                    if source.wait(timeout=30):
                        raise RuntimeError('Archiv nicht vollständig lesbar. Siehe lokales Protokoll.')
                partial.replace(archive)
                with archive.open('rb') as file:
                    checksum=hashlib.file_digest(file,'sha256').hexdigest()
                digest=archive.with_suffix('.gpg.sha256')
                digest.write_text(f'{checksum}  {archive.name}\n')
                run.update(file=archive.name, data_copy='apps' in targets, sha256=checksum,
                           size=archive.stat().st_size)
                if run['data_copy']:
                    run['local_saved_at'] = datetime.now(BERLIN).isoformat(timespec='seconds')
                if 'download' in targets:
                    ready = datetime.now(BERLIN)
                    run['download_ready_at'] = ready.isoformat(timespec='seconds')
                    if not run['data_copy']:
                        run.update(download_cache=True, download_expires_at=(ready+timedelta(hours=DOWNLOAD_HOURS)).isoformat(timespec='seconds'))
                self.save(run)
                if run['transfer']:
                    if self.profile == 'apps':
                        self.apps_upload(archive, checksum)
                        run['remote_sha256_verified'] = True
                    else:
                        scp = ['/usr/bin/sshpass','-f',str(CREDENTIAL),'/usr/bin/scp','-F','/dev/null',
                               '-o','StrictHostKeyChecking=yes','-o','PubkeyAuthentication=no',
                               '-o','PreferredAuthentications=password,keyboard-interactive',
                               '-o','NumberOfPasswordPrompts=1','-o','ConnectTimeout=8']
                        subprocess.run(scp+[str(archive),TARGET],stdout=log,stderr=log,check=True,timeout=900)
                        subprocess.run(scp+[str(digest),TARGET],stdout=log,stderr=log,check=True,timeout=120)
                    run['copied']=True
                    run['copied_at'] = datetime.now(BERLIN).isoformat(timespec='seconds')
                run['status']='finished'
                self.save(run)
                try:
                    self.prune(log)
                except (OSError, ValueError):
                    log.write('Aufbewahrung wegen uneindeutiger Metadaten ausgesetzt. Neue Sicherung ist gespeichert.\n')
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
            run.update(status='failed',error=(('Externe Kopie fehlgeschlagen.' +
                                               (' Lokales Archiv bleibt erhalten.' if run.get('data_copy') else
                                                ' ZIP-Download bleibt verfügbar.' if run.get('download_ready_at') else '')) if run.get('file')
                                               else (str(error) if isinstance(error, ValueError) else f'Sicherung fehlgeschlagen: {type(error).__name__}.')))
        finally:
            try:
                if partial is not None:
                    partial.unlink(missing_ok=True)
                if system_directory is not None:
                    system_directory.cleanup()
                if transfer_directory is not None:
                    transfer_directory.cleanup()
                if restore_directory is not None:
                    restore_directory.cleanup()
                self.save(run)
            finally:
                self.deploys.lock.release()
