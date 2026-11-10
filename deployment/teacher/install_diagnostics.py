"""One-time local setup of the independent, closed Teacher diagnostic key."""
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import stat
import subprocess
import tempfile

PUBLIC_KEY = None
DIAGNOSTICS = None
STATE = Path('/var/lib/cisco-lab-teacher')
AUTH = Path('/var/lib/cisco-lab-app/.ssh/authorized_keys')
ENDPOINT = Path('/usr/local/sbin/cisco-lab-teacher-diagnostics')
WRAPPER = Path('/usr/local/sbin/cisco-lab-teacher-diagnostics-ssh')
SUDO = Path('/etc/sudoers.d/cisco-lab-teacher-diagnostics')


def protected(path):
    for item in (path, *path.parents):
        info = item.lstat()
        if info.st_uid != 0 or info.st_mode & 0o022 or stat.S_ISLNK(info.st_mode):
            raise ValueError('Nicht rootgeschützt: ' + str(item))


def atomic(path, body, mode):
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as f:
        temporary = Path(f.name)
        try:
            os.fchmod(f.fileno(), mode)
            f.write(body); f.flush(); os.fsync(f.fileno())
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def main():
    if os.geteuid() != 0 or socket.gethostname().split('.')[0] != 'LAB-Teacher':
        raise SystemExit('Nur mit sudo direkt auf LAB-Teacher ausführen.')
    if not isinstance(PUBLIC_KEY, str) or not re.fullmatch(r'ssh-ed25519 [A-Za-z0-9+/]+={0,2}', PUBLIC_KEY):
        raise ValueError('Eigener öffentlicher Diagnoseschlüssel fehlt.')
    compile(DIAGNOSTICS, '<diagnostics>', 'exec')
    for path in (AUTH, STATE / 'install.json', ENDPOINT.parent, SUDO.parent):
        protected(path)
    line = 'restrict,from="203.0.113.200",command="' + str(WRAPPER) + '" ' + PUBLIC_KEY
    rows = AUTH.read_text().splitlines()
    if any(PUBLIC_KEY in row and row != line for row in rows):
        raise ValueError('Diagnoseschlüssel ist bereits anders zugelassen.')
    if line not in rows:
        rows.append(line)
    body = ('#!/usr/bin/python3 -IB\n' + DIAGNOSTICS).encode()
    changes = {ENDPOINT: (body, 0o755), WRAPPER: (body, 0o755),
               SUDO: (('cisco-lab-app ALL=(root) NOPASSWD: ' + str(ENDPOINT) + ' ""\n').encode(), 0o440),
               STATE / 'diagnostic-key.pub': ((PUBLIC_KEY + '\n').encode(), 0o600),
               AUTH: (('\n'.join(rows) + '\n').encode(), 0o644)}
    marker = STATE / 'install.json'
    data = json.loads(marker.read_text())
    data['files'][str(AUTH)] = hashlib.sha256(changes[AUTH][0]).hexdigest()
    changes[marker] = ((json.dumps(data, indent=2) + '\n').encode(), 0o600)
    previous = {}
    for path in changes:
        protected(path.parent)
        if path.exists() or path.is_symlink():
            protected(path)
            previous[path] = (path.read_bytes(), stat.S_IMODE(path.stat().st_mode))
        else:
            previous[path] = None
    with tempfile.NamedTemporaryFile(dir=SUDO.parent) as check:
        check.write(changes[SUDO][0]); check.flush()
        subprocess.run(['/usr/sbin/visudo', '-cf', check.name], check=True, capture_output=True)
    try:
        for path, (content, mode) in changes.items():
            atomic(path, content, mode)
        subprocess.run(['/usr/sbin/visudo', '-c'], check=True, capture_output=True)
    except BaseException:
        for path, original in previous.items():
            if original is None:
                path.unlink(missing_ok=True)
            else:
                atomic(path, *original)
        raise
    print('Teacher-Diagnosezugang eingerichtet. Nur feste Lesebefehle von APP; kein Neustart.')


if __name__ == '__main__':
    main()
