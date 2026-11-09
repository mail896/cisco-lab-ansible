"""Refresh an idle SDDM greeter, leaving any active login alone."""
import os
from pathlib import Path
import shlex
import socket
import subprocess
import sys
import time

GATE = Path('/run/nologin')
RUNTIME = Path('/run/cisco-lab-greeter')


def refresh_greeter(host, address, known_hosts):
    loader = "import sys; source = sys.stdin.read(); exec(compile(source, '<greeter-refresh>', 'exec'))"
    remote = shlex.join(['sudo','-n','/usr/bin/python3','-IB','-c',loader,host])
    command = ['/usr/bin/ssh','-T','-F','/dev/null','-o','BatchMode=yes',
               '-o','IdentitiesOnly=yes','-o','StrictHostKeyChecking=yes',
               '-o','GlobalKnownHostsFile=/dev/null','-o',f'UserKnownHostsFile={known_hosts}',
               '-o','ConnectTimeout=8','-i',str(Path.home()/'.ssh/id_ed25519'),
               f'administrator@{address}',remote]
    try:
        result = subprocess.run(command,input=Path(__file__).read_text(),text=True,capture_output=True,timeout=70)
    except (OSError,subprocess.SubprocessError):
        return 'SDDM-Neustart konnte nicht bestätigt werden.'
    if result.returncode:
        return 'SDDM-Neustart konnte nicht bestätigt werden.'
    return result.stdout.strip() or 'SDDM-Neustart nicht bestätigt.'


def read(*args):
    return subprocess.check_output(args, text=True, timeout=12).strip()


def idle():
    sessions = read('loginctl', 'list-sessions', '--no-legend').splitlines()
    for row in sessions:
        info = dict(line.split('=', 1) for line in read(
            'loginctl', 'show-session', row.split()[0], '-p', 'Class', '-p', 'Service',
            '-p', 'Seat', '-p', 'Type',
        ).splitlines())
        if not info.get('Class') or (info['Class'].startswith('user') and (
            info.get('Seat') or info.get('Type') in ('x11', 'wayland') or info.get('Service') != 'sshd'
        )):
            return False
    for proc in Path('/proc').iterdir():
        if not proc.name.isdecimal():
            continue
        try:
            if (proc/'comm').read_text().strip() == 'sddm-helper':
                args = (proc/'cmdline').read_bytes().split(b'\0')
                if args != [b''] and b'--greeter' not in args:
                    return False
        except (FileNotFoundError, ProcessLookupError):
            continue
    return not any(row.startswith('/var/lib/iserv/client/home/') for row in
                   read('findmnt', '-n', '-o', 'TARGET').splitlines())


def main():
    if os.geteuid() != 0 or socket.gethostname() != sys.argv[1]:
        raise SystemExit('Rechneridentität oder Rechte stimmen nicht.')
    if sys.argv[2:] != ['guarded']:
        subprocess.run([
            'systemd-run', '--quiet', '--wait', '--pipe', '--collect',
            '--unit=cisco-lab-greeter', '--property=RuntimeDirectory=cisco-lab-greeter',
            '--property=RuntimeMaxSec=55s', '/usr/bin/python3', '-IB', '-c',
            source, sys.argv[1], 'guarded',
        ], check=True, timeout=65)
        return
    for service in ('sddm', 'sddm-autologin'):
        rules = Path('/etc/pam.d',service).read_text().splitlines()
        if not any(line.split()[:3] == ['auth','requisite','pam_nologin.so'] for line in rules):
            print('SDDM unverändert: Anmeldesperre nicht bestätigt.')
            return
    if Path('/etc/nologin').exists() or GATE.is_symlink() or GATE.exists():
        print('SDDM unverändert: Andere Anmeldesperre vorhanden.')
        return
    if not idle() or read('systemctl','show','-p','ActiveState','--value','sddm.service') != 'active':
        print('SDDM unverändert: Keine eindeutig freie laufende Anmeldemaske.')
        return
    marker = RUNTIME/'nologin'
    marker.write_text('Das LAB-Anmeldebild wird aktualisiert. Bitte gleich erneut anmelden.\n')
    marker.chmod(0o644)
    try:
        GATE.symlink_to(marker)
    except FileExistsError:
        print('SDDM unverändert: Andere Anmeldesperre vorhanden.')
        return
    try:
        time.sleep(2)
        if not idle():
            print('SDDM unverändert: Eine Anmeldung läuft.')
            return
        subprocess.run(['systemctl','restart','sddm.service'],check=True,timeout=35)
        print('SDDM neu gestartet. Neues Anmeldebild ist beim Greeter geladen.')
    finally:
        if GATE.is_symlink() and os.readlink(GATE) == str(marker):
            GATE.unlink()


if __name__ == '__main__':
    main()
