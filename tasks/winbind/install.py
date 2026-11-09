"""Version-bound LAB Winbind reconnect fix with rollback and loaded-module verification."""
import base64
from contextlib import ExitStack
import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import socket
import stat
import subprocess
import tempfile
import sys
import time

MODULE = Path('/usr/lib/x86_64-linux-gnu/samba/idmap/ad.so')
BACKUPS = Path('/var/backups/cisco-lab/winbind-reconnect')
PACKAGES = ('winbind', 'samba-libs', 'samba-common-bin', 'libwbclient0')


def command(*argv, timeout=12, check=True):
    return subprocess.run(argv, text=True, capture_output=True, timeout=timeout, check=check)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def check_version():
    versions = command('dpkg-query', '-W', '-f=${Status} ${Version}\n', *PACKAGES).stdout.splitlines()
    if versions != ['install ok installed ' + manifest['vendor_version']] * len(PACKAGES):
        raise RuntimeError(f'Inkompatible Samba-Pakete: {versions}')


def check_module():
    info = MODULE.lstat()
    if (not stat.S_ISREG(info.st_mode) or (info.st_uid, info.st_gid) != (0, 0)
            or info.st_mode & 0o022 or info.st_nlink != 1):
        raise RuntimeError('Unerwartete Rechte oder Dateityp des IDMAP-Moduls.')
    return info


def require_idle():
    sessions = command('loginctl', 'list-sessions', '--no-legend').stdout
    for row in sessions.splitlines():
        sid = row.split()[0]
        values = dict(line.split('=', 1) for line in command(
            'loginctl', 'show-session', sid, '-p', 'Type', '-p', 'Class', '-p', 'Remote'
        ).stdout.splitlines())
        if values.get('Class', '').startswith('user') and (
            values.get('Type') in ('x11', 'wayland')
            or (values.get('Type') == 'tty' and values.get('Remote') == 'no')
        ):
            raise RuntimeError('Lokale Benutzersitzung aktiv; Änderung ausgelassen.')
    mounts = command('findmnt', '-n', '-o', 'TARGET').stdout.splitlines()
    if any(path.startswith('/var/lib/iserv/client/home/') for path in mounts):
        raise RuntimeError('IServ-Home oder Files noch eingebunden; Änderung ausgelassen.')


def atomic_replace(data, mode):
    with tempfile.NamedTemporaryFile(prefix='.cisco-lab-ad-', dir=MODULE.parent, delete=False) as file:
        path = Path(file.name)
        try:
            file.write(data)
            file.flush()
            os.fchmod(file.fileno(), mode)
            os.fsync(file.fileno())
            path.replace(MODULE)
        finally:
            path.unlink(missing_ok=True)


def loaded_pid():
    inode = MODULE.stat().st_ino
    for proc in Path('/proc').iterdir():
        if not proc.name.isdigit():
            continue
        try:
            if (proc / 'comm').read_text().strip() not in ('wb-idmap', 'winbindd'):
                continue
            for line in (proc / 'maps').read_text().splitlines():
                fields = line.split(maxsplit=5)
                if len(fields) == 6 and fields[5] == str(MODULE) and int(fields[4]) == inode:
                    return proc.name
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            continue
    return None


def verify():
    expected = expected_identity
    for attempt in range(8):
        try:
            user = command('getent', 'passwd', test_user, timeout=7, check=False)
            group = command('getent', 'group', 'cisco.lab', timeout=7, check=False)
            groups = command('id', '-G', test_user, timeout=7, check=False)
            if (user.returncode == group.returncode == groups.returncode == 0
                    and user.stdout.split(':')[2] == expected['user']
                    and group.stdout.split(':')[2] == expected['group']
                    and expected['group'] in groups.stdout.split()):
                break
        except subprocess.TimeoutExpired:
            pass
        time.sleep(4)
    else:
        raise RuntimeError('NSS-Benutzer oder LAB-Gruppe löst nach Winbind-Start nicht auf.')
    command('systemctl', 'is-active', 'winbind.service')
    command('wbinfo', '-t', timeout=20)
    pid = loaded_pid()
    if not pid:
        probe = command('wbinfo', f'--uid-to-sid={probe_uid}', timeout=12, check=False)
        if probe.returncode == 0:
            print('IDMAP-Probe ergab eine existierende ID; es wurden keine Daten verändert.', flush=True)
        pid = loaded_pid()
    if not pid:
        raise RuntimeError('Geladenes Modul im Winbind-Prozess konnte nicht bestätigt werden.')
    print(f'NSS, LAB-Gruppe und Rechnervertrauen geprüft; IDMAP-Modul in PID {pid} geladen.', flush=True)


def backup_metadata(directory, metadata):
    path = directory / 'manifest.json'
    path.write_text(json.dumps(metadata, indent=2) + '\n')
    path.chmod(0o600)


def interrupted(signum, frame):
    raise RuntimeError(f'Unterbrechung durch Signal {signum}')


def resolve_identity(name):
    for attempt in range(8):
        try:
            user = command('getent', 'passwd', name, timeout=7).stdout.strip().split(':')
            group = command('getent', 'group', 'cisco.lab', timeout=7).stdout.strip().split(':')
            groups = command('id', '-G', name, timeout=7).stdout.split()
            if int(user[2]) > 0 and int(group[2]) > 0 and group[2] in groups:
                return {'user': user[2], 'group': group[2]}
        except (subprocess.SubprocessError, ValueError, IndexError):
            pass
        time.sleep(4)
    raise RuntimeError('Testkonto und cisco.lab sind vor der Änderung nicht zuverlässig auflösbar.')


def main():
    global target, manifest, candidate_base64, patch_source, probe_uid, test_user, expected_identity
    data = json.load(sys.stdin)
    target = data['host']
    manifest = data['manifest']
    candidate_base64 = data['candidate']
    patch_source = data['patch']
    test_user = data['student']
    probe_uid = 2_000_000 + os.getpid() % 100_000
    if not re.fullmatch(r'[a-zA-Z0-9_.-]+', test_user):
        raise SystemExit('Ungültiges Testkonto.')
    if digest(patch_source.encode()) != manifest['patch_sha256']:
        raise SystemExit('Patchquelle entspricht nicht dem geprüften Stand.')
    if os.geteuid() != 0 or socket.gethostname() != target or not re.fullmatch(r'LAB-0[1-8][AB]', target):
        raise SystemExit('Falsches Ziel. Nur benannte LAB-Schülerclients zugelassen.')
    check_version()
    check_module()
    expected_identity = resolve_identity(test_user)
    candidate = base64.b64decode(candidate_base64, validate=True)
    if not candidate.startswith(b'\x7fELF') or digest(candidate) != manifest['candidate_sha256']:
        raise SystemExit('Patchdatei entspricht nicht dem verifizierten Kandidaten.')
    current = digest(MODULE.read_bytes())
    if current == manifest['candidate_sha256']:
        verify()
        print('Bereits gepatcht; keine Änderung.', flush=True)
        raise SystemExit(0)
    if current != manifest['vendor_module_sha256']:
        raise SystemExit('Unbekanntes installiertes Modul; keine Änderung.')

    require_idle()
    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, interrupted)
    with ExitStack() as stack:
        for lock in ('/var/lib/dpkg/lock-frontend', '/var/lib/dpkg/lock'):
            file = stack.enter_context(open(lock, 'r+b'))
            fcntl.lockf(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        check_version()
        info = check_module()
        original = MODULE.read_bytes()
        if digest(original) != manifest['vendor_module_sha256']:
            raise RuntimeError('Originaldatei änderte sich während der Vorprüfung.')
        mode = stat.S_IMODE(info.st_mode)
        BACKUPS.mkdir(mode=0o700, parents=True, exist_ok=True)
        backup = BACKUPS / datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
        backup.mkdir(mode=0o700)
        (backup / 'ad.so.vendor').write_bytes(original)
        (backup / 'ad.so.vendor').chmod(0o600)
        (backup / 'retry-on-closed-ldap.patch').write_text(patch_source)
        metadata = dict(manifest, host=target, original_mode=mode, applied=False)
        backup_metadata(backup, metadata)
        print(f'Original gesichert: {backup}', flush=True)
        require_idle()
        try:
            atomic_replace(candidate, mode)
            command('systemctl', 'restart', 'winbind.service', timeout=45)
            verify()
            if digest(MODULE.read_bytes()) != manifest['candidate_sha256']:
                raise RuntimeError('Patchdatei änderte sich während der Abnahme.')
            metadata['applied'] = True
            backup_metadata(backup, metadata)
            print('Winbind-Patch installiert und geprüft.', flush=True)
        except BaseException:
            if digest(MODULE.read_bytes()) == manifest['candidate_sha256']:
                atomic_replace(original, mode)
                command('systemctl', 'restart', 'winbind.service', timeout=45)
                print('Debian-Original automatisch wiederhergestellt.', flush=True)
            raise


if __name__ == '__main__':
    main()
