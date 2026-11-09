"""One managed maintenance pilot. Systemd supplies an isolated runtime directory."""
import argparse
from contextlib import contextmanager
from datetime import datetime
import hashlib
import json
import os
import pwd
from pathlib import Path
import socket
import subprocess
import sys
import time

from dashboard.room_action import request_logout, sessions
from maintenance.preview import BERLIN, PATCH_ABI_PACKAGES, PATCH_HASH, STOCK_HASH, VERSIONED_KERNEL, package_name, parse_apt, plan_host, start_allowed

HOME_ROOT = Path('/var/lib/iserv/client/home')
RUNTIME = Path('/run/cisco-lab-maintenance')
NOLOGIN = Path('/run/nologin')
MODULE = Path('/usr/lib/x86_64-linux-gnu/samba/idmap/ad.so')
ENV = dict(os.environ, LC_ALL='C', DEBIAN_FRONTEND='noninteractive', NEEDRESTART_MODE='l')


def read(*argv):
    return subprocess.check_output(argv, text=True, env=ENV, timeout=30).strip()


def apt(*argv):
    subprocess.run(['apt-get', '-o', 'DPkg::Lock::Timeout=30', *argv], env=ENV, check=True)


def session_classes():
    """A session may disappear between enumeration and show-session."""
    for row in read('loginctl', 'list-sessions', '--no-legend').splitlines():
        session = row.split()[0]
        try:
            kind = read('loginctl', 'show-session', session, '-p', 'Class', '--value')
        except subprocess.CalledProcessError:
            remaining = {line.split()[0] for line in read('loginctl', 'list-sessions', '--no-legend').splitlines()}
            if session not in remaining:
                continue
            raise
        if not kind:
            raise RuntimeError('Sitzung nicht eindeutig. Keine Paketinstallation.')
        yield kind


def occupied():
    if any(kind.startswith('user') for kind in session_classes()):
        return True
    return any(line.startswith('/var/lib/iserv/client/home/')
               for line in read('findmnt', '-n', '-o', 'TARGET').splitlines())


def current_bootable():
    packages = read('dpkg-query', '-W', '-f=${Package} ${Status}\n', 'linux-image-*')
    bootable = []
    for line in packages.splitlines():
        name, _, status = line.partition(' ')
        if status != 'install ok installed':
            continue
        release = name.removeprefix('linux-image-')
        if all(Path('/boot', prefix + release).is_file() for prefix in ('vmlinuz-', 'initrd.img-')):
            bootable.append(name)
    return bootable


def simulated(*args):
    return parse_apt(read('apt-get', '-s', *args))


def log(message):
    print(message, flush=True)


def write_result(status, detail=''):
    directory = Path('/var/lib/cisco-lab-maintenance')
    directory.mkdir(mode=0o700, exist_ok=True)
    if directory.is_symlink() or directory.stat().st_uid != 0 or directory.stat().st_mode & 0o022:
        raise RuntimeError('Wartungsstatus-Verzeichnis ist nicht rootgeschützt.')
    result = dict(host=socket.gethostname(), status=status, detail=detail,
                  time=datetime.now(BERLIN).isoformat(timespec='seconds'),
                  boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip())
    temporary = directory/'result.next'
    temporary.write_text(json.dumps(result, ensure_ascii=False))
    temporary.chmod(0o600)
    temporary.replace(directory/'result.json')


def gate_cleanup():
    if NOLOGIN.is_symlink() and os.readlink(NOLOGIN) == str(RUNTIME / 'nologin'):
        NOLOGIN.unlink(missing_ok=True)


@contextmanager
def login_gate():
    for service in ('sddm', 'sshd', 'login'):
        config = Path('/etc/pam.d', service).read_text()
        if not any('pam_nologin.so' in line and not line.lstrip().startswith('#')
                   for line in config.splitlines()):
            raise RuntimeError(f'Kein vorhandener PAM-Anmeldeschutz bei {service}.')
    marker = RUNTIME / 'nologin'
    marker.write_text('Kurze LAB-Wartung. Bitte nach Abschluss erneut anmelden.\n')
    marker.chmod(0o644)
    NOLOGIN.symlink_to(marker)
    try:
        # In-flight PAM sessions may have passed the first check already.
        for _ in range(3):
            if occupied():
                break
            time.sleep(2)
        yield
    finally:
        gate_cleanup()


def download_only():
    log('Nur Download: Aktive Anmeldung oder eingebundenes IServ-Home.')
    apt('-y', '--download-only', '--no-remove', '--with-new-pkgs', 'upgrade')


def finish_sessions():
    active = list(sessions())
    for session, info in active:
        if info.get('Service') not in ('sddm', 'sddm-autologin') or info.get('Type') not in ('x11', 'wayland'):
            raise RuntimeError('Nichtgrafische lokale Sitzung vorhanden. Wartung ausgesetzt.')
        user = pwd.getpwuid(int(info['User']))
        if user.pw_uid == 0:
            raise RuntimeError('Root-Sitzung vorhanden. Wartung ausgesetzt.')
        log(f'Reguläre KDE-Abmeldung für Sitzung {session} angefordert.')
        request_logout(user.pw_uid, user.pw_gid)
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        # Include SSH sessions and user managers still shutting down.
        pending = False
        for kind in session_classes():
            if kind.startswith('user') or kind == 'manager':
                pending = True
        if not pending and not list(sessions()):
            return
        time.sleep(2)
    raise RuntimeError('Abmeldung nicht vollständig abgeschlossen. Keine Installation und kein Neustart.')


def has_user_processes(uid):
    for proc in Path('/proc').iterdir():
        if not proc.name.isdecimal():
            continue
        try:
            if proc.stat().st_uid == uid:
                return True
        except FileNotFoundError:
            continue
    return False


def stale_homes():
    table = json.loads(read('findmnt', '--json', '--list', '-o', 'TARGET,FSTYPE'))['filesystems']
    if HOME_ROOT.resolve() != HOME_ROOT:
        raise RuntimeError('IServ-Profilwurzel ist umgeleitet.')
    local_names = {line.split(':', 1)[0] for line in Path('/etc/passwd').read_text().splitlines()}
    for mount in table:
        path = Path(mount['target'])
        if path.parent != HOME_ROOT:
            continue
        if mount['fstype'] != 'tmpfs' or path.is_symlink() or path.resolve() != path:
            raise RuntimeError('Unerwarteter Profilmount. Kein automatisches Aushängen.')
        owner = pwd.getpwnam(path.name)
        if owner.pw_name in local_names or owner.pw_uid == 0 or owner.pw_dir != str(path) or path.stat().st_uid != owner.pw_uid:
            raise RuntimeError('Profilbesitzer ist nicht eindeutig.')
        if has_user_processes(owner.pw_uid):
            raise RuntimeError('Benutzerprozesse laufen noch. Home bleibt eingehängt.')
        children = [row for row in table if Path(row['target']).is_relative_to(path) and row['target'] != str(path)]
        if any(Path(row['target']).parent != path or Path(row['target']).name not in ('Files', 'Groups', 'Print')
               or row['fstype'] != 'cifs' for row in children):
            raise RuntimeError('Unerwartete Unter-Mounts im Home.')
        yield path, [row['target'] for row in children]


def unmount_stale_homes():
    # Build and validate the whole list before unmounting anything. No recursive deletion.
    homes = list(stale_homes())
    if homes:
        settings = Path('/etc/iserv/config').read_text().splitlines()
        if 'SYNC=0' not in settings:
            raise RuntimeError('Profilarchivierung aktiv oder unklar. Verbliebene RAM-Homes bleiben zur Prüfung erhalten.')
    for path, children in homes:
        for target in [*children, str(path)]:
            subprocess.run(['umount', '--', target], check=True, timeout=30)
        log(f'Abgemeldetes RAM-Home ausgehängt: {path.name}')


def maintain(evening_restart=False):
    if read('dpkg', '--audit'):
        raise RuntimeError('dpkg-Zustand erfordert Prüfung.')
    apt('-o', 'APT::Update::Error-Mode=any', 'update')
    if not evening_restart and occupied():
        download_only()
        return False
    with login_gate():
        if evening_restart:
            finish_sessions()
            unmount_stale_homes()
        if occupied():
            if evening_restart:
                raise RuntimeError('Sitzung oder Home noch aktiv. Wartung zur Prüfung ausgesetzt.')
            download_only()
            return False
        digest = hashlib.sha256(MODULE.read_bytes()).hexdigest()
        upgrade = simulated('--with-new-pkgs', 'upgrade')
        abi = [value for value in upgrade['install'] if package_name(value) in PATCH_ABI_PACKAGES]
        if upgrade['remove'] or (abi and digest != STOCK_HASH):
            raise RuntimeError('Upgrade würde Pakete entfernen oder eine Winbind-ABI ändern. Zur Prüfung ausgesetzt.')
        if upgrade['install']:
            if occupied():
                download_only()
                return False
            apt('-y', '--no-remove', '--with-new-pkgs', 'upgrade')
        cleanup = simulated('autoremove')
        bootable = current_bootable()
        decision = plan_host(dict(occupied=occupied(), dpkg_audit=bool(read('dpkg', '--audit')),
                                  upgrade={'install': [], 'remove': []}, autoremove=cleanup,
                                  ad_hash=digest, running_kernel='linux-image-' + os.uname().release,
                                  bootable_kernels=bootable, reboot_required=False))
        if cleanup['remove']:
            if decision.get('autoremove_allowed') and not occupied():
                removals = cleanup['remove']
                exact = parse_apt(read('apt-get', '-s', '--no-auto-remove', 'remove', *removals))
                if set(exact['remove']) != set(removals) or exact['install']:
                    raise RuntimeError('APT weicht von der geprüften Entfernungsliste ab.')
                if not occupied():
                    log('Geprüfte Paketbereinigung: ' + ', '.join(removals))
                    apt('-y', '--no-auto-remove', 'remove', *removals)
            else:
                log('Paketbereinigung ausgesetzt: ' + ', '.join(decision.get('blocked_removals', [])))
        if read('dpkg', '--audit'):
            raise RuntimeError('dpkg-Nachprüfung meldet Probleme.')
        if digest == PATCH_HASH and hashlib.sha256(MODULE.read_bytes()).hexdigest() != digest:
            raise RuntimeError('Winbind-Patch hat sich geändert.')
        running = 'linux-image-' + os.uname().release
        installed_new_kernel = any(
            VERSIONED_KERNEL.fullmatch(package_name(value))
            and package_name(value) != running
            and package_name(value) in bootable
            for value in upgrade['install']
        )
        if evening_restart or ((Path('/run/reboot-required').exists() or installed_new_kernel) and not occupied()):
            log('Wartung erfolgreich. Vereinbarter Abendneustart.' if evening_restart else 'Neustart erforderlich. Keine aktive Anmeldung.')
            if evening_restart:
                write_result('success', 'Paketprüfung bestanden, Neustart angefordert.')
            subprocess.run(['systemctl', '--no-block', 'reboot'], check=True)
            return True
    log('Wartung abgeschlossen. Rechner bleibt eingeschaltet.')
    return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manual-pilot', action='store_true')
    parser.add_argument('--cleanup', action='store_true')
    parser.add_argument('--host', choices=tuple(f'LAB-{number:02d}{side}' for number in range(1, 9) for side in 'AB'), default='LAB-08B')
    parser.add_argument('--evening-restart', action='store_true')
    args = parser.parse_args()
    if os.geteuid() != 0 or socket.gethostname() != args.host:
        parser.error('Nur als systemd-Dienst auf dem ausdrücklich ausgewählten Pilotclient.')
    if args.cleanup:
        gate_cleanup()
        return
    if not RUNTIME.is_dir():
        parser.error('Systemd-Laufzeitverzeichnis fehlt.')
    if not args.manual_pilot and not start_allowed(datetime.now(BERLIN)):
        log('Außerhalb des Startfensters. Wartung übersprungen.')
        return
    try:
        if args.evening_restart:
            write_result('running')
        maintain(evening_restart=args.evening_restart)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        if args.evening_restart:
            write_result('failed', str(error))
        print(f'Wartung fehlgeschlagen: {error}', file=sys.stderr, flush=True)
        raise SystemExit(1) from error


if __name__ == '__main__':
    main()
