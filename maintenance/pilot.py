"""One managed maintenance pilot. Systemd supplies an isolated runtime directory."""
import argparse
from contextlib import contextmanager
from datetime import datetime
import hashlib
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

from maintenance.preview import BERLIN, PATCH_ABI_PACKAGES, PATCH_HASH, STOCK_HASH, VERSIONED_KERNEL, package_name, parse_apt, plan_host, start_allowed

HOST = 'LAB-08B'
RUNTIME = Path('/run/cisco-lab-maintenance')
NOLOGIN = Path('/run/nologin')
MODULE = Path('/usr/lib/x86_64-linux-gnu/samba/idmap/ad.so')
ENV = dict(os.environ, LC_ALL='C', DEBIAN_FRONTEND='noninteractive', NEEDRESTART_MODE='l')


def read(*argv):
    return subprocess.check_output(argv, text=True, env=ENV, timeout=30).strip()


def apt(*argv):
    subprocess.run(['apt-get', '-o', 'DPkg::Lock::Timeout=30', *argv], env=ENV, check=True)


def occupied():
    for row in read('loginctl', 'list-sessions', '--no-legend').splitlines():
        kind = read('loginctl', 'show-session', row.split()[0], '-p', 'Class', '--value')
        if not kind:
            raise RuntimeError('Sitzung nicht eindeutig. Keine Paketinstallation.')
        if kind.startswith('user'):
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


def maintain():
    if read('dpkg', '--audit'):
        raise RuntimeError('dpkg-Zustand erfordert Prüfung.')
    apt('-o', 'APT::Update::Error-Mode=any', 'update')
    if occupied():
        download_only()
        return False
    with login_gate():
        if occupied():
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
        if (Path('/run/reboot-required').exists() or installed_new_kernel) and not occupied():
            log('Neustart erforderlich. Keine aktive Anmeldung.')
            subprocess.run(['systemctl', '--no-block', 'reboot'], check=True)
            return True
    log('Wartung abgeschlossen. Rechner bleibt eingeschaltet.')
    return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manual-pilot', action='store_true')
    parser.add_argument('--cleanup', action='store_true')
    args = parser.parse_args()
    if os.geteuid() != 0 or socket.gethostname() != HOST:
        parser.error('Nur als systemd-Dienst auf LAB-08B.')
    if args.cleanup:
        gate_cleanup()
        return
    if not RUNTIME.is_dir():
        parser.error('Systemd-Laufzeitverzeichnis fehlt.')
    if not args.manual_pilot and not start_allowed(datetime.now(BERLIN)):
        log('Außerhalb des Startfensters. Wartung übersprungen.')
        return
    try:
        maintain()
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(f'Wartung fehlgeschlagen: {error}', file=sys.stderr, flush=True)
        raise SystemExit(1) from error


if __name__ == '__main__':
    main()
