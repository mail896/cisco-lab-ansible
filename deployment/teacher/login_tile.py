"""Enable the local Teacher account tile without changing authentication."""
import configparser
import os
from pathlib import Path
import pwd
import re
import socket
import stat
import tempfile


def render(text, uid):
    values = {'Users': {'MinimumUid': str(uid), 'MaximumUid': str(uid), 'RememberLastUser': 'true'},
              'Theme': {'EnableAvatars': 'true'}}
    for section, options in values.items():
        for key, value in options.items():
            match = re.search(r'(?m)^\[' + section + r'\]\s*$', text)
            if not match:
                text = text.rstrip('\n') + '\n[' + section + ']\n' + key + '=' + value + '\n'
                continue
            start = match.end()
            next_section = re.search(r'(?m)^\[', text[start:])
            end = start + next_section.start() if next_section else len(text)
            block = re.sub(r'(?m)^\s*' + key + r'\s*=.*\n?', '', text[start:end])
            block = block.strip('\n')
            text = text[:start] + '\n' + (block + '\n' if block else '') + key + '=' + value + '\n' + text[end:]
    config = configparser.ConfigParser(interpolation=None)
    config.optionxform = str
    config.read_string(text)
    return text


def main():
    if os.geteuid() != 0 or socket.gethostname().split('.')[0] != 'LAB-Teacher':
        raise SystemExit('Nur mit sudo direkt auf LAB-Teacher ausführen.')
    account = pwd.getpwnam('marc')
    path = Path('/etc/sddm.conf')
    if not path.exists() and not path.is_symlink():
        Path('/etc/sddm.conf.d').mkdir(mode=0o755, exist_ok=True)
        path = Path('/etc/sddm.conf.d/zzzzzz-cisco-lab-teacher-login.conf')
    for parent in path.parents:
        info = parent.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise ValueError('Unsicherer Systempfad: ' + str(parent))
    original = ''
    if path.exists() or path.is_symlink():
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise ValueError('Unsichere SDDM-Datei: ' + str(path))
        original = path.read_text()
    content = render(original, account.pw_uid)
    if content != original:
        backup = path.with_name(path.name + '.before-user-tile')
        if original and not backup.exists():
            with backup.open('xb') as f:
                os.fchmod(f.fileno(), 0o600)
                f.write(original.encode())
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as f:
            temporary = Path(f.name)
            try:
                os.fchmod(f.fileno(), 0o644)
                f.write(content.encode())
                f.flush()
                os.fsync(f.fileno())
                temporary.replace(path)
            finally:
                temporary.unlink(missing_ok=True)
    print('Teacher-Benutzerkachel für marc vorbereitet. Passwort bleibt erforderlich.')
    print('Aktiv nach Neustart von SDDM. Vorhandenes Benutzerbild wird verwendet, sofern lesbar.')


if __name__ == '__main__':
    main()
