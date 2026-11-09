"""Prepare only APP's dedicated Teacher key and authenticated installer download."""
import ast
import base64
import hashlib
import json
import os
from pathlib import Path
import pwd
import socket
import stat
import subprocess
import tempfile

SOURCE = Path(__file__).resolve().parents[2]
RUNTIME = Path('/opt/cisco-lab')
HOME = Path('/var/lib/cisco-lab')
ACCOUNT = 'cisco-lab'


def installer(public_key, root=SOURCE):
    text = (root / 'deployment/teacher/install.py').read_text()
    payload = {'receiver': (root / 'deployment/teacher/receiver.py').read_text(),
               'preview': (root / 'maintenance/preview.py').read_text(),
               'software_client': (root / 'dashboard/software_client.py').read_text(),
               'deb_client': (root / 'dashboard/deb_client.py').read_text()}
    for name, value in (('PUBLIC_KEY', public_key), ('PAYLOAD', payload)):
        tree = ast.parse(text)
        assignment = [node for node in tree.body if isinstance(node, ast.Assign)
                      and any(isinstance(target, ast.Name) and target.id == name for target in node.targets)]
        if len(assignment) != 1 or not isinstance(assignment[0].value, ast.Constant) or assignment[0].value.value is not None:
            raise ValueError('Teacher-Installer enthält nicht die geprüfte Einbettungsstelle.')
        lines = text.splitlines(keepends=True)
        node = assignment[0]
        lines[node.lineno - 1:node.end_lineno] = [name + ' = ' + repr(value) + '\n']
        text = ''.join(lines)
    compile(text, '<teacher-installer>', 'exec')
    return text.encode()


def refreshed_installer(previous, root=SOURCE):
    """Keep the trusted enrolled public key while refreshing embedded code."""
    tree = ast.parse(previous)
    keys = [node.value.value for node in tree.body if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == 'PUBLIC_KEY' for target in node.targets)
            and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)]
    if len(keys) != 1:
        raise ValueError('Rootgeschützter Teacher-Installer enthält keinen eindeutigen APP-Schlüssel.')
    return installer(keys[0], root)


def atomic(path, content, uid, gid, mode):
    if path.exists() or path.is_symlink():
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != uid or info.st_mode & 0o022:
            raise ValueError('Unerwartete Teacher-Vorbereitungsdatei: ' + str(path))
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.teacher-setup-', delete=False) as file:
        temporary = Path(file.name)
        try:
            os.fchown(file.fileno(), uid, gid); os.fchmod(file.fileno(), mode)
            file.write(content); file.flush(); os.fsync(file.fileno())
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def service_file(path, content, account):
    # Writable service directories must never be traversed while retaining root.
    child = os.fork()
    if child == 0:
        try:
            os.setgroups([])
            os.setgid(account.pw_gid)
            os.setuid(account.pw_uid)
            atomic(path, content, account.pw_uid, account.pw_gid, 0o600)
        except BaseException:
            os._exit(1)
        os._exit(0)
    _, status = os.waitpid(child, 0)
    if status != 0:
        raise ValueError('Private Teacher-Vorbereitungsdatei konnte nicht sicher geschrieben werden.')


def main():
    if os.geteuid() != 0 or socket.gethostname().split('.')[0] != 'apps':
        raise SystemExit('Teacher-Zugang nur als root in den eigenen APP-LAB-Pfaden vorbereiten.')
    account = pwd.getpwnam(ACCOUNT)
    if account.pw_dir != str(HOME):
        raise ValueError('Unerwartetes APP-LAB-Dienstkonto.')
    info = RUNTIME.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise ValueError('APP-LAB-Runtime ist nicht rootgeschützt.')
    ssh = HOME / '.ssh'
    info = ssh.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != account.pw_uid or info.st_mode & 0o077:
        raise ValueError('Privates APP-LAB-SSH-Verzeichnis fehlt.')
    key = ssh / 'id_ed25519_teacher'
    if not key.exists() and not key.is_symlink():
        if key.with_suffix('.pub').exists():
            raise ValueError('Öffentlicher Teacher-Schlüssel ohne privaten Schlüssel vorhanden.')
        subprocess.run(['/usr/sbin/runuser', '-u', ACCOUNT, '--', '/usr/bin/ssh-keygen',
                        '-q', '-t', 'ed25519', '-N', '', '-C', 'cisco-lab-teacher', '-f', str(key)], check=True)
    info = key.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != account.pw_uid or info.st_mode & 0o077:
        raise ValueError('Teacher-Dienstschlüssel ist nicht privat.')
    public = subprocess.check_output(['/usr/sbin/runuser', '-u', ACCOUNT, '--',
                                      '/usr/bin/ssh-keygen', '-y', '-f', str(key)], text=True).strip()
    public = ' '.join(public.split()[:2])
    if ' '.join(key.with_suffix('.pub').read_text().split()[:2]) != public:
        raise ValueError('Teacher-Public-Key gehört nicht zum Dienstschlüssel.')
    configuration = HOME / 'private/teacher-wake.json'
    config = json.loads(configuration.read_text())
    if config['host'] != 'LAB-Teacher' or config['address'] != '203.0.113.100':
        raise ValueError('Teacher-Zielkonfiguration weicht ab.')
    result = subprocess.run(['/usr/bin/ssh-keyscan', '-T', '5', '-t', 'ed25519', '203.0.113.100'],
                            capture_output=True, text=True, check=True, timeout=8)
    lines = [line for line in result.stdout.splitlines() if line and not line.startswith('#')]
    if len(lines) != 1 or lines[0].split()[:2] != ['203.0.113.100', 'ssh-ed25519']:
        raise ValueError('Teacher-Hostschlüssel nicht eindeutig.')
    encoded = lines[0].split()[2]
    fingerprint = 'SHA256:' + base64.b64encode(hashlib.sha256(base64.b64decode(encoded, validate=True)).digest()).decode().rstrip('=')
    if fingerprint != config['fingerprint']:
        raise ValueError('Teacher-Hostschlüssel widerspricht der unabhängigen Bestätigung.')
    service_file(ssh / 'teacher.known_hosts', (lines[0] + '\n').encode(), account)
    activation = HOME / 'private/teacher-maintenance.json'
    if not activation.exists() and not activation.is_symlink():
        service_file(activation, b'{"version":1,"enabled":false,"pilot_verified":false,"s5_verified":false}\n', account)
    destination = RUNTIME / 'deployment/teacher/teacher-installer.py'
    for folder in (RUNTIME / 'deployment', destination.parent):
        if not folder.exists() and not folder.is_symlink():
            folder.mkdir(mode=0o755)
        info = folder.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise ValueError('Teacher-Installer-Ziel ist nicht rootgeschützt.')
    atomic(destination, installer(public), 0, 0, 0o644)
    print('Eigener Teacher-Dienstschlüssel, bestätigter Host-Pin und Installer auf APP vorbereitet.')
    print('Teacher-Automatik bleibt gesperrt; keine Teacher-Verbindung angemeldet und keine Teacher-Änderung ausgeführt.')


if __name__ == '__main__':
    main()
