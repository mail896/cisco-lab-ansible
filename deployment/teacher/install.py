"""Install the closed Teacher receiver. Never enable a timer or start maintenance."""
import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import socket
import stat
import subprocess
import tempfile

ROOT = next((parent for parent in Path(__file__).resolve().parents
             if (parent / 'maintenance/preview.py').is_file()), None)
STATE = Path('/var/lib/cisco-lab-teacher')
HOME = Path('/var/lib/cisco-lab-app')
ACCOUNT = 'cisco-lab-app'
GUARD = 'session [success=1 default=ignore] pam_succeed_if.so quiet user = cisco-lab-app'
MOUNT = 'session optional pam_mount.so'
MOUNT_STACK = 'session substack cisco-lab-teacher-mount'
LIBRARY = Path('/usr/local/lib/cisco-lab-teacher')
PAM = Path('/etc/pam.d/common-session')
MARKER = STATE / 'install.json'
PUBLIC_KEY = None  # Replaced only in the reviewed, privately delivered installer.
PAYLOAD = None
RETIRED = {
    '/etc/systemd/system/cisco-lab-teacher-wake.timer': 'ba570a5b59ad667b6bafe9692e8da19fb66115251cdcb7c43aa53515e3a6a3dd',
    '/etc/systemd/system/cisco-lab-teacher-wake.service': '5776ef2748e2ecd5dd90239cc7da761c90fab96b0548c824d7524d7a8e0897a9',
}


def pam_content(text):
    lines = text.splitlines(keepends=True)
    active = [(i, line.strip()) for i, line in enumerate(lines)
              if line.strip() and not line.lstrip().startswith('#')]
    mounts = [(index, value) for index, value in active if 'pam_mount.so' in value]
    stacks = [(index, value) for index, value in active if 'cisco-lab-teacher-mount' in value]
    if stacks:
        if len(stacks) != 1 or stacks[0][1] != MOUNT_STACK or mounts:
            raise ValueError('Unbekannte Teacher-Mount-Untergruppe. Keine PAM-Änderung.')
        return text
    if len(mounts) != 1 or not re.fullmatch(r'session\s+optional\s+pam_mount\.so', mounts[0][1]):
        raise ValueError('Unbekannter Teacher-pam_mount-Aufbau. Keine PAM-Änderung.')
    index = mounts[0][0]
    if any(ACCOUNT in value for _, value in active):
        raise ValueError('Andere PAM-Vorgabe für das Teacher-Dienstkonto vorhanden.')
    # PAM counts an entire substack as one module in its parent stack. Replace
    # one module with one substack so every existing jump keeps its target,
    # including jumps from other included PAM files. Never renumber those rules.
    lines[index] = MOUNT_STACK + ('\n' if lines[index].endswith('\n') else '')
    return ''.join(lines)


def regular(path, *, private=False):
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_gid != 0
            or info.st_mode & (0o077 if private else 0o022)):
        raise ValueError('Teacher-Systemdatei ist nicht eindeutig rootgeschützt: ' + str(path))
    return info


def directory(path, mode=0o755):
    if not path.exists() and not path.is_symlink():
        directory(path.parent)
        path.mkdir(mode=mode)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise ValueError('Teacher-Systemverzeichnis ist nicht rootgeschützt: ' + str(path))


def check_account():
    account = pwd.getpwnam(ACCOUNT)
    if (account.pw_uid == 0 or account.pw_dir != str(HOME) or account.pw_shell != '/bin/sh'
            or os.getgrouplist(ACCOUNT, account.pw_gid) != [account.pw_gid]):
        raise ValueError('Teacher-Dienstkonto besitzt unerwartete Rechte oder Einstellungen.')
    state = subprocess.check_output(['passwd', '-S', ACCOUNT], text=True).split()
    if len(state) < 2 or state[1] != 'L':
        raise ValueError('Teacher-Dienstkonto besitzt einen Passwortzugang.')
    effective = subprocess.check_output([
        '/usr/sbin/sshd', '-T', '-C', 'user=cisco-lab-app,host=apps,addr=203.0.113.200'], text=True)
    settings = dict(line.split(' ', 1) for line in effective.splitlines() if ' ' in line)
    if (settings.get('usepam') != 'yes' or settings.get('pubkeyauthentication') != 'yes'
            or '.ssh/authorized_keys' not in settings.get('authorizedkeysfile', '').split()
            or settings.get('authenticationmethods') not in ('any', 'publickey')):
        raise ValueError('Bestehende Teacher-SSH-Regeln erlauben den begrenzten Schlüsselzugang nicht.')


def replace(path, content, mode):
    directory(path.parent)
    with tempfile.NamedTemporaryFile(prefix='.cisco-lab-', dir=path.parent, delete=False) as file:
        temporary = Path(file.name)
        try:
            os.fchmod(file.fileno(), mode)
            file.write(content); file.flush(); os.fsync(file.fileno())
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def check_parents(path):
    for parent in path.parents:
        if parent.exists() or parent.is_symlink():
            info = parent.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
                raise ValueError('Unsicherer Teacher-Systempfad: ' + str(parent))


def wrapper(statement):
    return ('#!/usr/bin/python3 -IB\nimport sys\nsys.path.insert(0, "/usr/local/lib/cisco-lab-teacher")\n'
            'import receiver\n' + statement + '\n').encode()


def files(public_key, payload):
    if set(payload) != {'receiver', 'preview', 'software_client', 'deb_client'}:
        raise ValueError('Unvollständiges Teacher-Installationspaket.')
    body = ('#!/usr/bin/python3 -IB\nimport os, subprocess, sys, re, shutil\n'
            'command = os.environ.get("SSH_ORIGINAL_COMMAND", "")\n'
            'if len(command) > 200: raise SystemExit("Auftrag zu lang")\n'
            'if re.fullmatch(r"software-upload [a-f0-9]{64} [1-9][0-9]{0,9}", command):\n'
            '    worker = subprocess.Popen(["/usr/bin/sudo", "-n", "/usr/local/sbin/cisco-lab-teacher-endpoint"], stdin=subprocess.PIPE)\n'
            '    try:\n'
            '        worker.stdin.write((command+"\\n").encode()); shutil.copyfileobj(sys.stdin.buffer,worker.stdin,1048576)\n'
            '    finally: worker.stdin.close()\n'
            '    raise SystemExit(worker.wait())\n'
            'if command == "software":\n'
            '    payload = sys.stdin.read(32769)\n'
            '    if len(payload) > 32768: raise SystemExit("Auftrag zu lang")\n'
            '    command += "\\n" + payload\n'
            'raise SystemExit(subprocess.run(["/usr/bin/sudo", "-n", '
            '"/usr/local/sbin/cisco-lab-teacher-endpoint"], input=command, text=True).returncode)\n')
    unit = '''[Unit]
Description=CISCO-LAB Teacher maintenance requested by APP
ConditionHost=LAB-Teacher
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
WorkingDirectory=/usr/local/lib/cisco-lab-teacher
ExecStart=/usr/local/sbin/cisco-lab-teacher-run
ExecStopPost=/usr/local/sbin/cisco-lab-teacher-cleanup
RuntimeDirectory=cisco-lab-teacher-maintenance
RuntimeDirectoryMode=0755
UMask=0077
TimeoutStartSec=infinity
'''
    key = 'restrict,from="203.0.113.200",command="/usr/local/sbin/cisco-lab-teacher-ssh" ' + public_key + '\n'
    return {
        LIBRARY / 'receiver.py': (payload['receiver'].encode(), 0o644),
        LIBRARY / 'software_client.py': (payload['software_client'].encode(), 0o644),
        LIBRARY / 'deb_client.py': (payload['deb_client'].encode(), 0o644),
        LIBRARY / 'maintenance/preview.py': (payload['preview'].encode(), 0o644),
        Path('/usr/local/sbin/cisco-lab-teacher-endpoint'): (wrapper('receiver.endpoint()'), 0o755),
        Path('/usr/local/sbin/cisco-lab-teacher-run'): (wrapper('receiver.run()'), 0o755),
        Path('/usr/local/sbin/cisco-lab-teacher-cleanup'): (wrapper('receiver.require_teacher(); receiver.cleanup_gate()'), 0o755),
        Path('/usr/local/sbin/cisco-lab-teacher-ssh'): (body.encode(), 0o755),
        HOME / '.ssh/authorized_keys': (key.encode(), 0o644),
        Path('/etc/sudoers.d/cisco-lab-teacher-app'): (
            b'cisco-lab-app ALL=(root) NOPASSWD: /usr/local/sbin/cisco-lab-teacher-endpoint ""\n', 0o440),
        Path('/etc/systemd/system/cisco-lab-teacher-maintenance.service'): (unit.encode(), 0o644),
        Path('/etc/apt/apt.conf.d/99zz-cisco-lab-teacher'): (
            b'// Teacher installation jobs are coordinated by the APP controller.\nAPT::Periodic::Unattended-Upgrade "0";\n', 0o644),
        Path('/etc/pam.d/cisco-lab-teacher-mount'): ((GUARD + '\n' + MOUNT + '\n').encode(), 0o644),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--public-key', type=Path)
    args = parser.parse_args()
    if os.geteuid() != 0 or socket.gethostname().split('.')[0] != 'LAB-Teacher':
        parser.error('Ausschließlich als root direkt auf LAB-Teacher ausführen.')
    release = dict(line.split('=', 1) for line in Path('/etc/os-release').read_text().splitlines() if '=' in line)
    if (release.get('ID', '').strip('"'), release.get('VERSION_ID', '').strip('"')) != ('debian', '13'):
        raise ValueError('Teacher-Installer erwartet Debian 13.')
    for path in (PAM, Path('/etc/sudoers')):
        regular(path)
    for module in ('pam_succeed_if.so', 'pam_mount.so', 'pam_nologin.so'):
        if not Path('/usr/lib/x86_64-linux-gnu/security', module).is_file():
            raise ValueError('Teacher-PAM-Modul fehlt: ' + module)
    for service in ('sddm', 'sshd', 'login'):
        if not any('pam_nologin.so' in line and not line.lstrip().startswith('#')
                   for line in Path('/etc/pam.d', service).read_text().splitlines()):
            raise ValueError('Teacher-Anmeldeschutz fehlt: ' + service)
    subprocess.run(['/usr/sbin/visudo', '-c', '-f', '/etc/sudoers'], check=True, capture_output=True)
    for unit in ('cisco-lab-teacher-maintenance.service', 'cisco-lab-teacher-wake.service',
                 'apt-daily.service', 'apt-daily-upgrade.service'):
        value = subprocess.check_output(['systemctl', 'show', unit, '-p', 'ActiveState', '--value'], text=True).strip()
        if value not in ('inactive', 'failed', ''):
            raise ValueError('Teacher-Dienst ist beschäftigt: ' + unit)
    public_key = args.public_key.read_text().strip() if args.public_key else PUBLIC_KEY
    if not isinstance(public_key, str) or not re.fullmatch(r'ssh-ed25519 [A-Za-z0-9+/]+={0,2}', public_key):
        raise ValueError('Genau ein eigener APP-Teacher-Public-Key erforderlich.')
    if PAYLOAD is None and ROOT is None:
        raise ValueError('Teacher-Installationsquellen fehlen.')
    payload = PAYLOAD or {'receiver': (ROOT / 'deployment/teacher/receiver.py').read_text(),
                          'preview': (ROOT / 'maintenance/preview.py').read_text(),
                          'software_client': (ROOT / 'dashboard/software_client.py').read_text(),
                          'deb_client': (ROOT / 'dashboard/deb_client.py').read_text()}
    changes = files(public_key, payload)
    original_pam = PAM.read_text()
    changes[PAM] = (pam_content(original_pam).encode(), stat.S_IMODE(PAM.stat().st_mode))
    previous = {}
    if MARKER.exists() or MARKER.is_symlink():
        regular(MARKER, private=True)
        previous = json.loads(MARKER.read_text())
    for path, (content, _) in changes.items():
        check_parents(path)
        if path.exists() or path.is_symlink():
            regular(path)
            if (path != PAM and path.read_bytes() != content
                    and hashlib.sha256(path.read_bytes()).hexdigest() != previous.get('files', {}).get(str(path))):
                raise ValueError('Fremde oder veränderte Teacher-Zieldatei: ' + str(path))
    for name, expected in RETIRED.items():
        path = Path(name)
        if path.exists() or path.is_symlink():
            regular(path)
            if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                raise ValueError('Alte Teacher-Wake-Unit ist nicht der geprüfte LAB-Stand.')
    local = {line.split(':', 1)[0] for line in Path('/etc/passwd').read_text().splitlines()}
    if ACCOUNT in local:
        account = pwd.getpwnam(ACCOUNT)
        if not previous or account.pw_dir != str(HOME) or account.pw_shell != '/bin/sh':
            raise ValueError('Vorhandenes Teacher-Dienstkonto zuerst prüfen.')
        check_account()
    else:
        try:
            pwd.getpwnam(ACCOUNT)
        except KeyError:
            pass
        else:
            raise ValueError('Namenskonflikt mit einem nichtlokalen Konto.')
    # Validate the narrow sudo rule before touching any live configuration.
    with tempfile.NamedTemporaryFile(mode='wb', prefix='cisco-lab-sudo-', delete=False) as file:
        validation = Path(file.name)
        file.write(changes[Path('/etc/sudoers.d/cisco-lab-teacher-app')][0]); file.flush()
    try:
        subprocess.run(['/usr/sbin/visudo', '-c', '-f', str(validation)], check=True, capture_output=True)
    finally:
        validation.unlink()
    directory(STATE, 0o700)
    if STATE.stat().st_mode & 0o077:
        raise ValueError('Teacher-Zustandsverzeichnis ist nicht privat.')
    directory(Path('/var/backups'))
    backup = Path(tempfile.mkdtemp(prefix='cisco-lab-teacher-setup-', dir='/var/backups'))
    original = {}
    for path in [*changes, *map(Path, RETIRED)]:
        if path.exists():
            target = backup / path.relative_to('/')
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
            original[path] = target
    created = False
    try:
        if ACCOUNT not in local:
            subprocess.run(['useradd', '--system', '--user-group', '--home-dir', str(HOME),
                            '--shell', '/bin/sh', '--no-create-home', ACCOUNT], check=True)
            created = True
        check_account()
        directory(HOME)
        directory(HOME / '.ssh')
        for path, (content, mode) in changes.items():
            if (not path.exists() or path.read_bytes() != content
                    or stat.S_IMODE(path.stat().st_mode) != mode):
                replace(path, content, mode)
        subprocess.run(['/usr/sbin/visudo', '-c', '-f', '/etc/sudoers'], check=True, capture_output=True)
        subprocess.run(['systemd-analyze', 'verify', '/etc/systemd/system/cisco-lab-teacher-maintenance.service'],
                       check=True, capture_output=True)
        apt = subprocess.check_output(['apt-config', 'dump'], text=True)
        if not re.search(r'^APT::Periodic::Unattended-Upgrade "0";$', apt, re.MULTILINE):
            raise ValueError('Konkurrierende automatische Teacher-Installation ist weiterhin aktiviert.')
        old_timer = Path('/etc/systemd/system/cisco-lab-teacher-wake.timer')
        if old_timer.exists():
            subprocess.run(['systemctl', 'disable', '--now', 'cisco-lab-teacher-wake.timer'], check=True)
        for name in RETIRED:
            Path(name).unlink(missing_ok=True)
        subprocess.run(['systemctl', 'daemon-reload'], check=True)
        # A dedicated login must be locked, unprivileged and limited to the forced key.
        marker = dict(version=1, account=ACCOUNT, installed_at=datetime.now().astimezone().isoformat(),
                      files={str(path): hashlib.sha256(content).hexdigest() for path, (content, _) in changes.items()})
        replace(MARKER, (json.dumps(marker, indent=2) + '\n').encode(), 0o600)
    except BaseException:
        for path in changes:
            if path in original:
                shutil.copy2(original[path], path)
            elif path.exists() and not path.is_symlink():
                path.unlink()
        for path in map(Path, RETIRED):
            if path in original:
                shutil.copy2(original[path], path)
        if created:
            subprocess.run(['userdel', ACCOUNT], check=False, capture_output=True)
        subprocess.run(['systemctl', 'daemon-reload'], check=False)
        raise
    print('Teacher-Empfänger installiert. Keine Updates, kein Neustart und kein Timer aktiviert.')
    print('Kleine Konfigurations-Rückfallkopie:', backup)


if __name__ == '__main__':
    main()
