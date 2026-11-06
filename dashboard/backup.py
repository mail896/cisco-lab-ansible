"""Encrypted LAB-Teacher configuration snapshots and optional SCP delivery."""
from datetime import datetime
import hashlib
import json
from pathlib import Path
import secrets
import stat
import subprocess
import threading
import uuid

from maintenance.preview import BERLIN

HOME = Path.home()
TARGET = 'marc@192.168.1.75:/Volumes/Repo/'
DATA_MOUNT = Path('/media/marc/data')
DATA_UUID = '001e0e07-067c-4549-97f0-3f4b0f499e12'
SOURCES = (
    HOME/'ansible-cisco-lab', HOME/'.ssh', HOME/'.veyon',
    HOME/'Bilder/CISCO-LAB',
    HOME/'.config/systemd/user/cisco-lab-dashboard.service',
    HOME/'.config/systemd/user/cisco-lab-dashboard.service.d',
    HOME/'backup-marc-labteacher.sh',
    HOME/'LAB-Bestandsaufnahme-2026-11-03/ssh-LAB-08B-frisches-image.known_hosts',
    HOME/'LAB-Bestandsaufnahme-2026-11-03/fresh-LAB-08B.json',
    Path('/etc/nginx/conf.d/cisco-lab-dashboard.conf'),
    Path('/etc/xdg/Veyon Solutions/Veyon.conf'), Path('/etc/veyon'),
    Path('/etc/hostname'), Path('/etc/hosts'), Path('/etc/fstab'),
)
CREDENTIAL = HOME/'.ssh/pw_macos-srv.cred'


class Backups:
    def __init__(self, directory, deploys, state_dir):
        self.directory = directory
        self.deploys = deploys
        self.state_dir = state_dir
        self.key = directory/'recovery-key'
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.ensure_key()
        for run in self.history():
            if run['status'] == 'running':
                run['status'] = 'interrupted'
                self.save(run)

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

    def save(self, run):
        path = self.directory/(run['id']+'.json')
        temp = path.with_suffix('.tmp')
        temp.write_text(json.dumps(run, ensure_ascii=False))
        temp.replace(path)

    def history(self):
        runs = [json.loads(path.read_text()) for path in self.directory.glob('*.json')]
        return sorted(runs,key=lambda run: run['started'],reverse=True)[:20]

    @staticmethod
    def archive_directory():
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

    def start(self, transfer):
        if type(transfer) is not bool:
            raise ValueError('Ungültige Auswahl für SCP.')
        if not self.deploys.lock.acquire(blocking=False):
            return None
        try:
            self.ensure_key()
            self.archive_directory()
            if transfer:
                self.check_private(CREDENTIAL)
            run = dict(id=uuid.uuid4().hex, started=datetime.now(BERLIN).isoformat(timespec='seconds'),
                       status='running', transfer=transfer)
            self.save(run)
            threading.Thread(target=self.execute,args=(run,),daemon=True).start()
            return dict(run)
        except BaseException:
            self.deploys.lock.release()
            raise

    def execute(self, run):
        stem = 'LAB-Teacher-Konfiguration-' + datetime.now(BERLIN).strftime('%Y%m%d-%H%M%S') + '-' + run['id'][:8]
        partial = None
        try:
            archive = self.archive_directory()/(stem+'.tar.gz.gpg')
            partial = archive.with_suffix('.partial')
            with (self.directory/(run['id']+'.log')).open('w') as log:
                paths = SOURCES + tuple(self.state_dir/name for name in (
                    'passwort', 'deploys', 'room', 'appearance', 'appearance-releases', 'provision',
                ))
                paths = [str(path.relative_to('/')) for path in paths if path.exists()]
                tar = ['/usr/bin/tar','--acls','--xattrs','-czf','-','-C','/',
                       '--exclude=__pycache__','--exclude=*.pyc',
                       '--exclude=home/marc/.ssh/pw_macos-srv.cred','--'] + paths
                with subprocess.Popen(tar, stdout=subprocess.PIPE, stderr=log) as source:
                    try:
                        encrypt = ['/usr/bin/gpg','--batch','--no-tty','--no-symkey-cache',
                                   '--pinentry-mode','loopback','--passphrase-file',str(self.key),
                                   '--symmetric','--cipher-algo','AES256','--compress-algo','none',
                                   '--output',str(partial)]
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
                run.update(file=archive.name, data_copy=True, sha256=checksum,
                           size=archive.stat().st_size)
                if run['transfer']:
                    scp = ['/usr/bin/sshpass','-f',str(CREDENTIAL),'/usr/bin/scp','-F','/dev/null',
                           '-o','StrictHostKeyChecking=yes','-o','PubkeyAuthentication=no',
                           '-o','PreferredAuthentications=password,keyboard-interactive',
                           '-o','NumberOfPasswordPrompts=1','-o','ConnectTimeout=8']
                    subprocess.run(scp+[str(archive),TARGET],stdout=log,stderr=log,check=True,timeout=900)
                    subprocess.run(scp+[str(digest),TARGET],stdout=log,stderr=log,check=True,timeout=120)
                    run['copied']=True
                run['status']='finished'
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
            run.update(status='failed',error=('SCP fehlgeschlagen; lokales Archiv bleibt erhalten.' if run.get('file')
                                               else (str(error) if isinstance(error, ValueError) else f'Sicherung fehlgeschlagen: {type(error).__name__}.')))
        finally:
            try:
                if partial is not None:
                    partial.unlink(missing_ok=True)
                self.save(run)
            finally:
                self.deploys.lock.release()
