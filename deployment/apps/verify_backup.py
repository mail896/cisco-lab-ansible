"""Restore one APP LAB archive to a private scratch directory and compare inputs."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tarfile
from tempfile import TemporaryDirectory
from datetime import datetime

from dashboard.backup import APPS_SYSTEM_SOURCES, Backups
from dashboard.deploy import Deployments
from maintenance.preview import BERLIN


def sha256(path):
    with path.open('rb') as file:
        return hashlib.file_digest(file, 'sha256').hexdigest()


def main(run_id):
    Deployments.validate_id(run_id)
    home = Path.home()
    state = home / '.local/state/cisco-lab-dashboard'
    backups = Backups(state / 'backup', Deployments(state / 'deploys', None), state, profile='apps')
    run = json.loads((backups.directory / (run_id + '.json')).read_text())
    if run.get('status') != 'finished' or not backups.archive_name.fullmatch(run.get('file', '')):
        raise ValueError('Keine abgeschlossene APP-LAB-Sicherung.')
    archive = backups.archive_directory() / run['file']
    if archive.is_symlink() or not archive.is_file() or sha256(archive) != run['sha256']:
        raise ValueError('Archiv oder Prüfsumme stimmt nicht.')
    digest = archive.with_suffix('.gpg.sha256')
    if digest.read_text() != f"{run['sha256']}  {archive.name}\n":
        raise ValueError('Prüfsummendatei stimmt nicht.')

    with TemporaryDirectory(prefix='lab-restore-test-', dir=home) as temporary:
        root = Path(temporary)
        encrypted = archive
        if run.get('copied'):
            encrypted = root / 'mac-copy.tar.gz.gpg'
            with encrypted.open('wb') as output:
                subprocess.run(backups.apps_ssh() + [f"get {archive.name} {run['sha256']}"],
                               stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.PIPE,
                               check=True, timeout=900)
            if sha256(encrypted) != run['sha256']:
                raise ValueError('Vom Mac zurückgelesene LAB-Sicherung weicht ab.')
        plain = root / 'archive.tar.gz'
        subprocess.run([
            '/usr/bin/gpg', '--homedir', str(backups.directory / '.gnupg'),
            '--batch', '--no-tty', '--pinentry-mode', 'loopback',
            '--passphrase-file', str(backups.key), '--decrypt',
            '--output', str(plain), str(encrypted),
        ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=900)
        restored = root / 'restored'
        restored.mkdir()
        with tarfile.open(plain, 'r:gz') as contents:
            members = contents.getmembers()
            names = [member.name for member in members]
            retired_timer = 'opt/cisco-lab/maintenance/systemd/cisco-lab-maintenance.timer'
            if retired_timer in names or (Path('/') / retired_timer).exists():
                raise ValueError('Alte lokale LAB-Timervorlage ist noch vorhanden.')
            if any('recovery-key' in name or name == 'opt/cisco-lab/local-private'
                   or member.issym() or member.islnk() for name, member in zip(names, members)):
                raise ValueError('Archiv enthält einen Schlüssel oder Link, der nicht wiederhergestellt werden darf.')
            if any(not (name == 'opt/cisco-lab' or name.startswith('opt/cisco-lab/')
                        or name == 'var/lib/cisco-lab' or name.startswith('var/lib/cisco-lab/')
                        or name in {str(path).lstrip('/') for path in APPS_SYSTEM_SOURCES})
                   for name in names):
                raise ValueError('Archiv enthält einen Pfad außerhalb der LAB-Runtime.')
            contents.extractall(restored, filter='data')
        checked = [
            '/opt/cisco-lab/dashboard/server.py',
            '/opt/cisco-lab/dashboard/provision.py',
            '/opt/cisco-lab/dashboard/intake.py',
            '/opt/cisco-lab/dashboard/auth.py',
            '/opt/cisco-lab/dashboard/service_access.py',
            '/opt/cisco-lab/dashboard/presence.py',
            '/opt/cisco-lab/dashboard/login_audit.py',
            '/opt/cisco-lab/dashboard/pagination.py',
            '/opt/cisco-lab/dashboard/history.js',
            '/opt/cisco-lab/dashboard/auth.css',
            '/opt/cisco-lab/dashboard/ansible_passwords.py',
            '/opt/cisco-lab/dashboard/collect_device.py',
            '/opt/cisco-lab/dashboard/intake.js',
            '/opt/cisco-lab/dashboard/room.py',
            '/opt/cisco-lab/dashboard/app.css',
            '/opt/cisco-lab/dashboard/app.js',
            '/opt/cisco-lab/dashboard/normalize_image.py',
            '/opt/cisco-lab/dashboard/index.html',
            '/opt/cisco-lab/dashboard/assets/cisco-lab.svg',
            '/opt/cisco-lab/dashboard/assets/cisco-lab-icon.png',
            '/opt/cisco-lab/deployment/apps/requirements.lock',
            '/opt/cisco-lab/deployment/apps/start_backup.py',
            '/opt/cisco-lab/deployment/apps/verify_backup.py',
            '/opt/cisco-lab/lab-baseline.yml',
            '/opt/cisco-lab/maintenance-client.yml',
            '/opt/cisco-lab/maintenance/teacher_wake.py',
            '/var/lib/cisco-lab/private/teacher-wake.json',
            '/var/lib/cisco-lab/private/maintenance-targets.json',
            '/var/lib/cisco-lab/.ssh/id_ed25519',
        ]
        checked.extend(map(str, APPS_SYSTEM_SOURCES))
        for name in ('/var/lib/cisco-lab/.local/state/cisco-lab-dashboard/passwort',
                     '/var/lib/cisco-lab/.local/state/cisco-lab-dashboard/auth/automation-token.json',
                     '/var/lib/cisco-lab/private/oidc.json'):
            if Path(name).is_file(): checked.append(name)
        teacher_inputs = (
            '/opt/cisco-lab/maintenance/teacher_controller.py',
            '/opt/cisco-lab/deployment/teacher/receiver.py',
            '/opt/cisco-lab/deployment/teacher/teacher-installer.py',
            '/var/lib/cisco-lab/.ssh/id_ed25519_teacher',
            '/var/lib/cisco-lab/.ssh/teacher.known_hosts',
            '/var/lib/cisco-lab/private/teacher-maintenance.json',
            '/var/lib/cisco-lab/.local/state/cisco-lab-dashboard/teacher/activation.json',
        )
        checked.extend(name for name in teacher_inputs if Path(name).is_file())
        activation = state / 'teacher/activation.json'
        if activation.is_file():
            pilot_id = json.loads(activation.read_text())['pilot_id']
            Deployments.validate_id(pilot_id)
            checked.append(str(state / 'teacher' / ('run-' + pilot_id + '.json')))
        package = '/var/lib/cisco-lab/private/packages/packettracer-9.0.deb'
        if Path(package).is_file():
            if sha256(Path(package)) != 'dd9ac0d4c7fc37dcb68f627fd7c7e6fa6d4200c14492526e5618b9bd172ed920':
                raise ValueError('Lokales Packet-Tracer-Paket weicht vom geprüften Stand ab.')
            checked.append(package)
        for name in checked:
            if sha256(Path(name)) != sha256(restored / name.lstrip('/')):
                raise ValueError(f'Wiederhergestellte LAB-Datei weicht ab: {name}')
        print(f'APP-LAB-Restore aus {"Mac-Kopie" if run.get("copied") else "lokalem Archiv"} geprüft: '
              f'{len(members)} Archiveinträge, {len(checked)} Dateiabgleiche.')
    run['restore_verified_at'] = datetime.now(BERLIN).isoformat(timespec='seconds')
    if run.get('copied'):
        run['remote_restore_verified_at'] = run['restore_verified_at']
    backups.save(run)
    print('Restore technisch bestätigt.')


if __name__ == '__main__':
    main(sys.argv[1])
