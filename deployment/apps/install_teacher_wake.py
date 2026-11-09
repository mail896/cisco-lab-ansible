"""Install the verified Teacher WOL target on APP without enabling a timer."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import stat
import tempfile

SOURCE = Path('/home/lab-admin/cisco-lab/maintenance/teacher_wake.py')
CODE = Path('/opt/cisco-lab/maintenance/teacher_wake.py')
CONFIG = Path('/var/lib/cisco-lab/private/teacher-wake.json')


def install(path, content, uid, gid, mode):
    if path.is_symlink():
        raise ValueError(f'Unerwarteter LAB-Zielpfad: {path}')
    if path.exists():
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != uid or info.st_gid != gid:
            raise ValueError(f'Unerwartete LAB-Zieldatei: {path}')
        if path.read_bytes() == content and stat.S_IMODE(info.st_mode) == mode:
            return
        raise ValueError(f'LAB-Zieldatei weicht vom geprüften Stand ab: {path}')
    with tempfile.NamedTemporaryFile(prefix='.teacher-wake-', dir=path.parent, delete=False) as output:
        temporary = Path(output.name)
        try:
            os.fchmod(output.fileno(), mode)
            os.fchown(output.fileno(), uid, gid)
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--school-mac', required=True)
    parser.add_argument('--fingerprint', required=True)
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error('Nur root darf die geschützte LAB-Runtime vorbereiten.')
    if (not re.fullmatch(r'(?:[0-9a-f]{2}:){5}[0-9a-f]{2}', args.school_mac)
            or not re.fullmatch(r'SHA256:[A-Za-z0-9+/]{43}', args.fingerprint)):
        parser.error('Bestätigte Schul-MAC und ED25519-Fingerabdruck erforderlich.')
    if (SOURCE.is_symlink() or not SOURCE.is_file() or CODE.parent.is_symlink()
            or CODE.parent.stat().st_uid != 0 or CONFIG.parent.is_symlink()):
        raise ValueError('Unerwartete LAB-Quell- oder Zielverzeichnisse.')
    account = pwd.getpwnam('cisco-lab')
    config = dict(version=1, host='LAB-Teacher', address='203.0.113.100',
                  school_mac=args.school_mac, fingerprint=args.fingerprint)
    source = SOURCE.read_bytes()
    install(CODE, source, 0, 0, 0o644)
    install(CONFIG, (json.dumps(config, indent=2) + '\n').encode(),
            account.pw_uid, account.pw_gid, 0o600)
    if hashlib.sha256(CODE.read_bytes()).digest() != hashlib.sha256(source).digest():
        raise ValueError('Installierter LAB-WOL-Code weicht von der Quelle ab.')
    print('Teacher-WOL-Ziel und Code vorbereitet. Kein Timer aktiviert und kein WOL-Paket gesendet.')


if __name__ == '__main__':
    main()
