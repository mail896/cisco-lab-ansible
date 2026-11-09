#!/usr/bin/python3
"""LAB serial console launcher. No sudo, shell execution, or serial probing."""
import argparse
from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
import grp
import html
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile

DEVICE_NAME = re.compile(r'tty(?:USB|ACM)\d+\Z')
TITLE = 'CISCO-LAB Konsole'


class ConsoleError(Exception):
    pass


@dataclass(frozen=True)
class Adapter:
    device: str
    label: str


def discover(dev=Path('/dev'), sys_tty=Path('/sys/class/tty')):
    """Enumerate USB-backed serial character devices; deduplicate by real path."""
    found = {}
    for path in sorted(dev.iterdir()):
        if not DEVICE_NAME.fullmatch(path.name):
            continue
        try:
            node = (sys_tty / path.name / 'device').resolve(strict=True)
            # USB device ancestors carry idVendor/idProduct; no modem commands.
            usb = next((p for p in (node, *node.parents)
                        if (p / 'idVendor').is_file() and (p / 'idProduct').is_file()), None)
            if usb is None or not stat.S_ISCHR(path.stat().st_mode):
                continue
            details = []
            for key in ('manufacturer', 'product', 'serial'):
                file = usb / key
                if file.is_file():
                    details.append(' '.join(file.read_text().split())[:100])
            real = str(path.resolve(strict=True))
            found[real] = Adapter(real, path.name + (' – ' + ' / '.join(details) if details else ''))
        except (OSError, StopIteration):
            continue  # disconnected or incomplete sysfs entry
    return list(found.values())


def select_adapter(adapters, requested=None, run=subprocess.run):
    if requested:
        for adapter in adapters:
            if adapter.device == requested:
                return adapter
        raise ConsoleError('Der ausgewaehlte USB-Konsolenadapter ist nicht mehr angeschlossen.')
    if not adapters:
        raise ConsoleError('Kein USB-Konsolenadapter gefunden. Bitte das serielle Konsolenkabel anschliessen. Der USB-Netzwerkadapter ist kein Konsolenadapter.')
    if len(adapters) == 1:
        return adapters[0]
    args = ['/usr/bin/kdialog', '--title', TITLE, '--menu', 'Konsolenadapter waehlen:']
    for index, adapter in enumerate(adapters):
        args.extend([str(index), adapter.label])
    result = run(args, capture_output=True, text=True)
    if result.returncode == 1:
        return None  # cancellation is not an error
    if result.returncode != 0:
        raise ConsoleError('Die Adapterauswahl konnte nicht geoeffnet werden.')
    choice = result.stdout.strip()
    if not choice.isdecimal() or int(choice) >= len(adapters):
        raise ConsoleError('Ungueltige Adapterauswahl.')
    return adapters[int(choice)]


def check_access(adapter, group, groups=None, access=os.access):
    try:
        gid = grp.getgrnam(group).gr_gid
    except KeyError:
        raise ConsoleError(f'Die IServ-Gruppe {group} ist momentan nicht aufloesbar. Bitte die Lehrkraft informieren.')
    current = set(os.getgroups()) | {os.getgid()} if groups is None else set(groups)
    if gid not in current:
        raise ConsoleError(f'Diese Sitzung hat keine Berechtigung ueber die Gruppe {group}. Nach einer Gruppenfreigabe ist eine erneute Anmeldung erforderlich. Bitte die Lehrkraft informieren.')
    if not access(adapter.device, os.R_OK | os.W_OK, effective_ids=True):
        raise ConsoleError(f'Kein Lese-/Schreibzugriff auf {adapter.device}. Bitte die Lehrkraft informieren; die Anwendung benoetigt keine Ausfuehrung als root.')


def check_device(adapter):
    try:
        if not stat.S_ISCHR(Path(adapter.device).stat().st_mode):
            raise OSError('not a character device')
    except OSError:
        raise ConsoleError('Der Konsolenadapter wurde entfernt. Bitte erneut anschliessen und die Anwendung neu starten.')


@contextmanager
def device_lock(adapter, directory=Path('/run/lock/cisco-lab-serial'), uucp=Path('/run/lock')):
    name = Path(adapter.device).name
    if not DEVICE_NAME.fullmatch(name):
        raise ConsoleError('Ungueltiges Konsolengeraet.')
    # Respect existing Minicom/UUCP locks. Never remove unknown or stale locks.
    if (uucp / ('LCK..' + name)).exists():
        raise ConsoleError('Fuer diesen Adapter besteht bereits eine Konsolensperre. Bitte andere Konsolen schliessen; verbleibende Sperren durch die Lehrkraft pruefen lassen.')
    try:
        fd = os.open(directory / (name + '.lock'), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o660)
    except OSError as error:
        raise ConsoleError('Die gemeinsame Konsolensperre ist nicht zugreifbar. Bitte die Lehrkraft informieren.') from error
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ConsoleError('Unerwartete Konsolensperrdatei. Bitte die Lehrkraft informieren.')
        if info.st_uid == os.geteuid():
            os.fchmod(fd, 0o660)  # retain group access even with a private umask
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ConsoleError('Dieser Adapter wird bereits ueber einen LAB-Konsolenstarter verwendet. Bitte die andere Konsole zuerst schliessen.')
        yield
    finally:
        os.close(fd)  # Do not unlink: that could split the lock between processes.


MINICOM_PROFILE = '''# Managed LAB profile; personal default profiles remain untouched.
pu baudrate         9600
pu bits             8
pu parity           N
pu stopbits         1
pu rtscts           No
pu xonxoff          No
pu hasdcd           No
pu minit
pu mreset
'''


def write_minicom_profile(home):
    path = home / '.minirc.lab-cisco'
    if path.is_symlink():
        raise ConsoleError('Das LAB-Minicom-Profil ist ein Symlink. Bitte die Lehrkraft informieren.')
    if path.exists() and path.read_text() == MINICOM_PROFILE:
        return
    # The dedicated LAB profile is owned by the current user, never by root.
    # Preserve the previous content before replacing an earlier LAB profile.
    if path.exists():
        import time
        backup = home / '.config/cisco-lab-backups/block02'
        backup.mkdir(parents=True, exist_ok=True, mode=0o700)
        old = backup / f'minirc-{time.time_ns()}.conf'
        old.write_bytes(path.read_bytes())
        old.chmod(0o600)
    fd, temporary = tempfile.mkstemp(prefix='.minirc.lab-cisco-', dir=home)
    try:
        with os.fdopen(fd, 'w') as stream:
            stream.write(MINICOM_PROFILE)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def app_command(application, device):
    if application == 'putty':
        return ['/usr/bin/putty', '-serial', device, '-sercfg', '9600,8,n,1,N']
    return ['/usr/bin/minicom', '-D', device, '-b', '9600', 'lab-cisco']


def notify(message):
    print(message, file=sys.stderr)
    if os.environ.get('DISPLAY') or os.environ.get('WAYLAND_DISPLAY'):
        try:
            subprocess.run(['/usr/bin/kdialog', '--title', TITLE, '--error', html.escape(message)], check=False)
        except OSError:
            pass


def main():
    args_parser = argparse.ArgumentParser(description=TITLE)
    args_parser.add_argument('application', choices=('putty', 'minicom'))
    args_parser.add_argument('--device')
    args_parser.add_argument('--in-terminal', action='store_true')
    args = args_parser.parse_args()
    if os.geteuid() == 0:
        raise ConsoleError('Bitte die Konsole als normaler IServ-Benutzer starten, nicht als root.')
    settings = json.loads(Path('/etc/cisco-lab/serial.json').read_text())
    adapter = select_adapter(discover(), args.device)
    if adapter is None:
        return 0
    check_device(adapter)
    check_access(adapter, settings['group'])
    if args.application == 'minicom' and not args.in_terminal:
        # A separate terminal keeps the child launcher and its lock alive.
        os.execv('/usr/bin/konsole', ['/usr/bin/konsole', '--separate', '--nofork', '--noclose',
                 '-e', '/usr/local/bin/lab-serial-console', 'minicom',
                 '--device', adapter.device, '--in-terminal'])
    if args.application == 'minicom':
        write_minicom_profile(Path.home())
    environment = os.environ.copy()
    environment.pop('MINICOM', None)  # no inherited init scripts/dial arguments
    with device_lock(adapter):
        check_device(adapter)
        result = subprocess.run(app_command(args.application, adapter.device), env=environment)
    if result.returncode != 0:
        raise ConsoleError('Die Konsole wurde mit einem Fehler beendet. Bitte Kabel, Zugriffsrechte und andere geoeffnete Konsolen pruefen. Die Anwendungsmeldung enthaelt weitere Hinweise.')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ConsoleError, OSError, ValueError) as error:
        notify(str(error))
        sys.exit(1)
