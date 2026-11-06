"""Run an allowlisted action on an identity-verified LAB client over SSH."""
import grp
import json
from pathlib import Path
import os
import pwd
import socket
import subprocess
import sys
import time


def read(*args):
    return subprocess.check_output(args, text=True, timeout=15).strip()


def session_ids():
    return [row.split()[0] for row in read('loginctl', 'list-sessions', '--no-legend').splitlines()]


def sessions():
    for session in session_ids():
        try:
            lines = read('loginctl', 'show-session', session, '-p', 'Class', '-p', 'Type',
                         '-p', 'Seat', '-p', 'Service', '-p', 'User').splitlines()
        except subprocess.CalledProcessError:
            if session not in session_ids():
                continue
            raise
        info = dict(line.split('=', 1) for line in lines)
        if not info.get('Class'):
            raise RuntimeError('Sitzungsstatus nicht eindeutig.')
        if info['Class'].startswith('user') and (
            info.get('Seat') or info.get('Type') in ('x11', 'wayland')
            or info.get('Service') != 'sshd'
        ):
            yield session, info


def home_mounted():
    return any(line.startswith('/var/lib/iserv/client/home/')
               for line in read('findmnt', '-n', '-o', 'TARGET').splitlines())


def maintenance_running():
    for unit in ('cisco-lab-maintenance.service', 'apt-daily.service', 'apt-daily-upgrade.service'):
        state = read('systemctl', 'show', '-p', 'ActiveState', '--value', unit)
        if state not in ('inactive', 'failed'):
            return True
    return False


DEMO = 'cisco'
DEMO_HOME = '/var/lib/cisco-lab-local/cisco'
DEMO_RUNTIME = Path('/run/cisco-lab-demo')
DEMO_CONFIG = Path('/etc/sddm.conf.d/zzzzzz-cisco-lab-demo-once.conf')
GATE = Path('/run/nologin')


def demo_account():
    local = {line.split(':',1)[0] for line in Path('/etc/passwd').read_text().splitlines()}
    marker = Path('/etc/cisco-lab/local-cisco.json')
    if DEMO not in local or not marker.is_file() or marker.is_symlink():
        return None
    user = pwd.getpwnam(DEMO)
    group = grp.getgrnam('cisco.lab').gr_gid
    if (not 400 <= user.pw_uid < 500 or user.pw_dir != DEMO_HOME or user.pw_gid != group
            or json.loads(marker.read_text()) != {'uid':user.pw_uid,'gid':group}
            or set(os.getgrouplist(DEMO,group)) != {group}):
        raise RuntimeError('Lokales Sammelkonto entspricht nicht dem geprüften Profil.')
    return user


def greeter_free():
    if list(sessions()) or home_mounted() or maintenance_running():
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
    return True


def demo_login(host, guarded):
    if not greeter_free():
        print('Übersprungen: Sitzung, Anmeldung oder Wartung läuft.')
        return
    user = demo_account()
    if user is None:
        print('Übersprungen: Lokales Sammelkonto cisco fehlt.')
        return
    if not guarded:
        result = subprocess.run([
            'systemd-run','--quiet','--wait','--pipe','--collect','--unit=cisco-lab-demo',
            '--description=CISCO-LAB einmalige Anmeldung',
            '--property=RuntimeDirectory=cisco-lab-demo','--property=RuntimeMaxSec=65s',
            '/usr/bin/python3','-IB','-c',source,host,'demo','guarded',
        ],timeout=72)
        raise SystemExit(result.returncode)
    if not Path('/usr/share/xsessions/plasmax11.desktop').is_file():
        raise RuntimeError('Geprüfte KDE-X11-Sitzung fehlt.')
    for file in ('sddm','sddm-autologin'):
        if not any(line.split()[:3] == ['auth','requisite','pam_nologin.so']
                   for line in Path('/etc/pam.d',file).read_text().splitlines()):
            raise RuntimeError('SDDM-Anmeldeschutz nicht bestätigt.')
    config_files = [*Path('/usr/lib/sddm/sddm.conf.d').glob('*.conf'),
                    *Path('/etc/sddm.conf.d').glob('*.conf'), Path('/etc/sddm.conf')]
    for config in config_files:
        if not config.is_file():
            continue
        in_autologin = False
        for raw in config.read_text().splitlines():
            line = raw.strip()
            if line.startswith('['):
                in_autologin = line == '[Autologin]'
                continue
            key, sep, value = line.partition('=')
            if not in_autologin or not sep:
                continue
            key, value = key.strip(), value.strip()
            active = key == 'User' and value or key == 'Relogin' and value.lower() in ('true','yes','1')
            if active or (config == Path('/etc/sddm.conf') and key in ('User','Session')):
                raise RuntimeError('Vorhandene Autologin-Vorgabe. Kein Eingriff.')
    if DEMO_CONFIG.exists() or DEMO_CONFIG.is_symlink() or Path('/etc/nologin').exists():
        raise RuntimeError('Einmalanmeldung oder andere Anmeldesperre ist bereits vorhanden.')
    marker = DEMO_RUNTIME/'nologin'
    marker.write_text('LAB-Anmeldebildschirm wird kurz vorbereitet.\n')
    marker.chmod(0o644)
    temporary_config = DEMO_RUNTIME/'sddm.conf'
    stopped = False
    try:
        GATE.symlink_to(marker)
        time.sleep(2)
        if not greeter_free():
            print('Übersprungen: Eine Anmeldung wurde begonnen.')
            return
        subprocess.run(['systemctl','stop','sddm.service'],check=True,timeout=22)
        stopped = True
        temporary_config.write_text(f'[Autologin]\nUser={DEMO}\nSession=plasmax11.desktop\nRelogin=false\n')
        DEMO_CONFIG.symlink_to(temporary_config)
        GATE.unlink()
        subprocess.run(['systemctl','start','sddm.service'],check=True,timeout=22)
        stopped = False
        for _ in range(24):
            active = list(sessions())
            if any(info.get('User') == str(user.pw_uid) and info.get('Type') == 'x11' for _,info in active):
                print('Lokale KDE-Sitzung als cisco angemeldet.')
                return
            time.sleep(1)
        print('Fehler: Die cisco-Sitzung wurde nicht bestätigt.')
    finally:
        for link, target in ((GATE,marker),(DEMO_CONFIG,temporary_config)):
            if link.is_symlink() and os.readlink(link) == str(target):
                link.unlink()
        if stopped:
            subprocess.run(['systemctl','start','sddm.service'],check=True,timeout=20)


def main():
    host, action = sys.argv[1:3]
    deferred = sys.argv[3:] == ['deferred']
    if os.geteuid() != 0 or socket.gethostname() != host:
        raise SystemExit('Rechneridentität oder Rechte stimmen nicht.')
    if maintenance_running():
        print('Übersprungen: Wartung läuft.')
        return
    if action == 'demo':
        demo_login(host,sys.argv[3:] == ['guarded'])
        return
    active = list(sessions())
    if action in ('poweroff', 'reboot'):
        if active or home_mounted():
            print('Übersprungen: Benutzersitzung oder IServ-Home aktiv.')
            return
        if deferred:
            subprocess.run(['systemctl', '--no-block', action], check=True)
            return
        subprocess.run([
            'systemd-run', '--quiet', '--collect', '--unit=cisco-lab-room-power', '--on-active=15s',
            '--timer-property=AccuracySec=1s', '/usr/bin/python3', '-IB', '-c',
            source, host, action, 'deferred',
        ], check=True, timeout=15)
        print('Systemaktion eingeplant. Sitzung wird vor Ausführung erneut geprüft.', flush=True)
    elif action in ('logout','demo-end'):
        if not active:
            print('Übersprungen: Keine lokale Sitzung.')
            return
        group = grp.getgrnam('cisco.lab').gr_gid if action == 'logout' else None
        people = [(session, pwd.getpwuid(int(info['User'])), info) for session, info in active]
        services = ('sddm',) if action=='logout' else ('sddm','sddm-autologin')
        if any(info.get('Service') not in services or info.get('Type') not in ('x11', 'wayland')
               or user.pw_uid == 0
               or (action=='logout' and group not in os.getgrouplist(user.pw_name, user.pw_gid))
               or (action=='demo-end' and (user.pw_name != DEMO or user.pw_dir != DEMO_HOME))
               for _, user, info in people):
            print('Übersprungen: Keine eindeutige LAB-Schülersitzung.')
            return
        if len(people) != 1:
            print('Übersprungen: Mehrere lokale Sitzungen.')
            return
        session, user, _ = people[0]
        runtime = f'/run/user/{user.pw_uid}'
        subprocess.run([
            'setpriv', f'--reuid={user.pw_uid}', f'--regid={user.pw_gid}', '--init-groups',
            'env', f'XDG_RUNTIME_DIR={runtime}', f'DBUS_SESSION_BUS_ADDRESS=unix:path={runtime}/bus',
            'busctl', '--user', 'call', 'org.kde.Shutdown', '/Shutdown', 'org.kde.Shutdown', 'logout',
        ], check=True, timeout=15)
        for _ in range(15):
            if session not in session_ids() and not home_mounted():
                print('Abmeldung und Ende der Home-Einbindung bestätigt.')
                return
            time.sleep(2)
        print('Abmeldung angefordert. Sitzungsende noch nicht bestätigt.')
    else:
        raise SystemExit('Unbekannte Aktion.')


if __name__ == '__main__':
    main()
