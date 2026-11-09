"""Remove the retired Teacher controller, dashboard, Nginx and Semaphore only."""
import argparse
from contextlib import ExitStack, contextmanager
from datetime import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import shlex
import shutil
import socket
import stat
import subprocess

PROJECT = Path('/home/lab-admin/ansible-cisco-lab')
OLD_STATE = Path('/home/lab-admin/.local/state/cisco-lab-dashboard')
USER_UNITS = ('cisco-lab-controller.timer', 'cisco-lab-controller.service', 'cisco-lab-dashboard.service')
USER_SYSTEMD = Path('/home/lab-admin/.config/systemd/user')
OLD_FILES = (
    Path('/etc/nginx/conf.d/cisco-lab-dashboard.conf'),
    Path('/etc/systemd/system/semaphore.service'),
    Path('/etc/sudoers.d/cisco-lab-system-backup'),
    Path('/usr/local/sbin/cisco-lab-backup-system'),
)
PACKAGE = re.compile(r'(?:ansible|ansible-core|semaphore|nginx(?:-[a-z0-9-]+)?|libnginx-mod-[a-z0-9-]+)')


@contextmanager
def retirement_locks():
    # The current receiver owns these same two locks. No maintenance request or
    # worker may begin between preflight and removal of the old packages.
    with ExitStack() as stack:
        for name in ('endpoint', 'worker'):
            descriptor = os.open('/var/lib/cisco-lab-teacher/'+name+'.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
            file = stack.enter_context(os.fdopen(descriptor, 'r+b'))
            info = os.fstat(file.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077:
                raise ValueError('Teacher-Wartungssperre ist nicht eindeutig privat.')
            fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def command(*args, check=True):
    return subprocess.run(args, text=True, capture_output=True, check=check,
                          env=dict(os.environ, LC_ALL='C', DEBIAN_FRONTEND='noninteractive'), timeout=300)


def package_plan(output, selected):
    """Never let dependency resolution remove unrelated workstation packages."""
    if re.search(r'^Inst ', output, re.MULTILINE):
        raise ValueError('APT möchte zusätzliche Pakete installieren. Keine Bereinigung.')
    removed = set(re.findall(r'^(?:Remv|Purg) ([a-z0-9+.:~-]+)', output, re.MULTILINE))
    if not removed <= set(selected):
        raise ValueError('APT möchte fremde Pakete entfernen. Keine Bereinigung: ' + ', '.join(sorted(removed-set(selected))))


def nginx_scope(output):
    configs = re.findall(r'^# configuration file ([^:]+):$', output, re.MULTILINE)
    allowed = {'/etc/nginx/nginx.conf', '/etc/nginx/mime.types',
               '/etc/nginx/conf.d/cisco-lab-dashboard.conf', '/etc/nginx/sites-enabled/default'}
    if not configs or any(name not in allowed and not name.startswith('/etc/nginx/modules-enabled/') for name in configs):
        raise ValueError('Nginx verwendet weitere Konfigurationen. Keine pauschale Entfernung.')
    lines = [line.split('#', 1)[0].strip() for line in output.splitlines()]
    for line in lines:
        if line.startswith('listen ') and line != 'listen 198.51.100.53:443 ssl;':
            raise ValueError('Nginx besitzt einen weiteren Listener. Keine Entfernung.')
        if line.startswith('server_name ') and line != 'server_name teacher.example.org;':
            raise ValueError('Nginx besitzt eine weitere Website. Keine Entfernung.')
        if line.startswith('proxy_pass ') and line != 'proxy_pass http://127.0.0.1:8765;':
            raise ValueError('Nginx verwendet einen anderen Empfänger. Keine Entfernung.')


def validate_tree(path, mounts):
    if path.is_symlink():
        raise ValueError('Alter LAB-Pfad ist ein Link: ' + str(path))
    if not path.exists():
        return
    if any(mount == path or path in mount.parents for mount in mounts):
        raise ValueError('Eingehängtes Dateisystem im alten LAB-Pfad: ' + str(path))
    for parent in path.parents:
        if parent.is_symlink():
            raise ValueError('Umgeleiteter alter LAB-Pfad: ' + str(path))


def semaphore_paths(unit_text):
    """Read file locations, never print database credentials from the config."""
    starts = re.findall(r'^ExecStart=(.+)$', unit_text, re.MULTILINE)
    if len(starts) != 1:
        raise ValueError('Semaphore-Startbefehl ist nicht eindeutig.')
    arguments = shlex.split(starts[0])
    configs = [arguments[index+1] for index, value in enumerate(arguments[:-1]) if value == '--config']
    configs += [value.split('=', 1)[1] for value in arguments if value.startswith('--config=')]
    if len(configs) != 1:
        raise ValueError('Semaphore-Konfigurationspfad fehlt oder ist nicht eindeutig.')
    config = Path(configs[0])
    roots = tuple(map(Path, ('/etc/semaphore', '/var/lib/semaphore', '/opt/semaphore',
                            '/home/semaphore', '/home/lab-admin/semaphore', '/home/lab-admin/.config/semaphore',
                            '/home/administrator/semaphore')))
    if not config.is_absolute() or not any(config.is_relative_to(root) for root in roots):
        raise ValueError('Anderen Semaphore-Konfigurationspfad zuerst prüfen: ' + str(config))
    data = json.loads(config.read_text())
    paths = [config]
    for key in ('bolt', 'sqlite'):
        entry = data.get(key)
        location = entry.get('host') if isinstance(entry, dict) else None
        if location:
            path = Path(location)
            if not path.is_absolute() or not any(path.is_relative_to(root) for root in roots):
                raise ValueError('Lokalen Semaphore-Datenbankpfad zuerst prüfen: ' + str(path))
            if path.exists():
                paths.append(path)
    return paths


def preserved_files():
    marker = Path('/var/lib/cisco-lab-teacher/install.json')
    data = json.loads(marker.read_text())
    paths = [Path(name) for name in data['files']]
    paths.extend([marker, Path('/etc/apt/apt.conf.d/99zz-cisco-lab-teacher')])
    result = {}
    for path in paths:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0:
            raise ValueError('Aktueller Teacher-Empfänger ist nicht eindeutig: ' + str(path))
        result[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true', help='Geprüfte alte LAB-Installation entfernen.')
    args = parser.parse_args()
    if os.geteuid() != 0 or socket.gethostname().split('.')[0] != 'LAB-Teacher':
        parser.error('Ausschließlich mit sudo direkt auf LAB-Teacher ausführen.')
    with retirement_locks():
        retire(args)


def retire(args):
    user = pwd.getpwnam('marc')
    if user.pw_uid != 1006 or user.pw_dir != '/home/marc':
        raise ValueError('Unerwartetes Teacher-Benutzerkonto.')
    before = preserved_files()
    if command('systemctl', 'show', 'cisco-lab-teacher-maintenance.service', '-p', 'ActiveState', '--value').stdout.strip() not in ('inactive', 'failed'):
        raise ValueError('Aktuelle Teacher-Wartung läuft. Später bereinigen.')
    userctl = ('runuser', '-u', 'marc', '--', 'env', 'XDG_RUNTIME_DIR=/run/user/1006', 'systemctl', '--user')
    if command(*userctl, 'show', 'cisco-lab-controller.service', '-p', 'ActiveState', '--value').stdout.strip() not in ('inactive', 'failed'):
        raise ValueError('Alter Teacher-Controller läuft noch. Keine Bereinigung während eines Auftrags.')
    installed = command('dpkg-query', '-W', '-f=${binary:Package}\t${db:Status-Status}\n',
                        'ansible', 'ansible-core', 'nginx*', 'libnginx-mod-*', 'semaphore', check=False)
    packages = sorted({name for line in installed.stdout.splitlines() if '\t' in line
                       for name, state in [line.split('\t', 1)] if state == 'installed' and PACKAGE.fullmatch(name)})
    if 'nginx' in packages:
        nginx_scope(command('nginx', '-T').stdout)
    semaphore = Path('/etc/systemd/system/semaphore.service')
    semaphore_files = semaphore_paths(semaphore.read_text()) if semaphore.exists() else []
    if packages:
        package_plan(command('apt-get', '-s', '--no-auto-remove', 'purge', *packages).stdout, packages)
    mounts = [Path(line) for line in command('findmnt', '-rn', '-o', 'TARGET').stdout.splitlines()]
    roots = [PROJECT, OLD_STATE, Path('/etc/ssl/cisco-lab'), Path('/etc/semaphore'), Path('/var/lib/semaphore')]
    for path in roots:
        validate_tree(path, mounts)
    # Preserve earlier backup archives and keys in place. Only the retired
    # dashboard's other state is removed; no fresh full archive is produced.
    state_paths = [path for path in OLD_STATE.iterdir() if path.name != 'backup'] if OLD_STATE.exists() else []
    units = [USER_SYSTEMD/name for name in USER_UNITS]
    drops = USER_SYSTEMD/'cisco-lab-dashboard.service.d'
    remove = [PROJECT, *state_paths, *OLD_FILES, *units, drops, Path('/etc/ssl/cisco-lab'),
              Path('/etc/semaphore'), Path('/var/lib/semaphore'), *semaphore_files]
    for path in remove:
        validate_tree(path, mounts)
    for target in ('sleep.target', 'suspend.target', 'hibernate.target', 'hybrid-sleep.target', 'suspend-then-hibernate.target'):
        if command('systemctl', 'show', target, '-p', 'LoadState', '--value').stdout.strip() != 'masked':
            raise ValueError('Vereinbarte Schlafsperre fehlt: ' + target)
    command('/usr/sbin/visudo', '-c')
    print('Pakete entfernen: ' + (', '.join(packages) or 'keine mehr'))
    for path in remove:
        if path.exists():
            print('Entfernen: ' + str(path))
    print('Bestehende Backup-Archive/Schlüssel, aktueller APP-Empfänger und Schlafsperren bleiben erhalten.')
    if not args.apply:
        print('Nur Vorschau. Mit --apply ausführen.')
        return
    small_trees = [Path('/etc/ssl/cisco-lab'), Path('/etc/semaphore'), drops]
    fallback_files = [*OLD_FILES, *units, drops/'backup.conf', *semaphore_files]
    for directory in small_trees:
        if directory.exists():
            fallback_files.extend(path for path in directory.rglob('*') if path.is_file())
    fallback_files = list(dict.fromkeys(path for path in fallback_files if path.is_file()))
    if any(path.is_symlink() for path in fallback_files) or sum(path.stat().st_size for path in fallback_files) > 64*1024*1024:
        raise ValueError('Konfigurations-Rückfallkopie ist unerwartet groß oder umgeleitet. Keine Änderung.')
    # One small protected configuration fallback; never copy the old project,
    # Packet Tracer package or full dashboard backups again.
    fallback = Path('/var/backups')/('cisco-lab-teacher-retired-' + datetime.now().strftime('%Y%m%d-%H%M%S'))
    fallback.mkdir(mode=0o700)
    command(*userctl, 'disable', '--now', *USER_UNITS, check=False)
    for unit in USER_UNITS:
        if command(*userctl, 'show', unit, '-p', 'ActiveState', '--value').stdout.strip() not in ('inactive', 'failed'):
            raise ValueError('Alter Benutzerdienst ist nicht beendet: ' + unit)
    for unit in ('nginx.service', 'semaphore.service'):
        if command('systemctl', 'show', unit, '-p', 'LoadState', '--value').stdout.strip() != 'not-found':
            command('systemctl', 'disable', '--now', unit)
    for path in fallback_files:
        destination = fallback/str(path).lstrip('/')
        destination.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        shutil.copy2(path, destination)
    if packages:
        # Full APT output remains in this local terminal, never in APP history.
        subprocess.run(['apt-get', '-y', '--no-auto-remove', 'purge', *packages],
                       env=dict(os.environ, LC_ALL='C', DEBIAN_FRONTEND='noninteractive'), check=True)
    for path in remove:
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=True)
    if OLD_STATE.exists() and not any(OLD_STATE.iterdir()):
        OLD_STATE.rmdir()
    command(*userctl, 'daemon-reload')
    command('systemctl', 'daemon-reload')
    command('/usr/sbin/visudo', '-c')
    if preserved_files() != before:
        raise RuntimeError('Aktueller Teacher-Empfänger hat sich geändert. Bitte Ausgabe melden.')
    print('Teacher-Altinstallation entfernt. Kein Neustart und keine Abmeldung ausgeführt.')
    print('Kleine Konfigurations-Rückfallkopie: ' + str(fallback))
    if (OLD_STATE/'backup').exists():
        print('Frühere Sicherungen unverändert: ' + str(OLD_STATE/'backup'))


if __name__ == '__main__':
    main()
