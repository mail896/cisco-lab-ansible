#!/usr/bin/python3
"""Finish the verified Wireshark pilot, with a local rollback on LAB-08A."""
import base64
import errno
import json
import os
from pathlib import Path
import socket
import stat
import subprocess
import sys
import tempfile

DUMPCAP = Path('/usr/bin/dumpcap')
STARTER = Path('/usr/share/applications/wireshark-lab.desktop')
SOCKET_UNIT = 'cisco-lab-capture.socket'
BACKUP_ROOT = Path('/var/backups/cisco-lab/block06')
QUESTION = 'wireshark-common/install-setuid'


def run(*argv, input=None, check=True):
    return subprocess.run(argv, input=input, text=True, stdout=subprocess.PIPE,
                          check=check, env=dict(os.environ, DEBIAN_FRONTEND='noninteractive'))


def debconf(command):
    result = run('debconf-communicate', 'wireshark-common', input=command + '\n')
    code, _, value = result.stdout.strip().partition(' ')
    if code != '0':
        raise RuntimeError(f'Debconf rejected {command}: {result.stdout}')
    return value


def capabilities():
    try:
        return os.getxattr(DUMPCAP, 'security.capability')
    except OSError as error:
        if error.errno != errno.ENODATA:
            raise
        return b''


def set_capabilities(value):
    if value:
        os.setxattr(DUMPCAP, 'security.capability', value)
    elif capabilities():
        os.removexattr(DUMPCAP, 'security.capability')


def write_starter(content, uid, gid, mode):
    with tempfile.NamedTemporaryFile(dir=STARTER.parent, delete=False) as output:
        temporary = Path(output.name)
        try:
            output.write(content)
            output.flush()
            os.fchown(output.fileno(), uid, gid)
            os.fchmod(output.fileno(), mode)
            temporary.replace(STARTER)
        finally:
            temporary.unlink(missing_ok=True)


def restore(backup):
    state = json.loads((backup / 'state.json').read_text())
    debconf(f'SET {QUESTION} {state["debconf"]}')
    os.chown(DUMPCAP, state['dumpcap_uid'], state['dumpcap_gid'])
    os.chmod(DUMPCAP, state['dumpcap_mode'])
    set_capabilities(base64.b64decode(state['capabilities']))
    write_starter((backup / 'previous.desktop').read_bytes(), *state['starter'])
    run('systemctl', 'enable' if state['enabled'] else 'disable', SOCKET_UNIT)


def probe(uid, username):
    # Run in the actual user manager so the broker sees the active local seat.
    script = """
import socket, struct
try:
    with socket.socket(socket.AF_PACKET, socket.SOCK_RAW):
        pass
except PermissionError:
    pass
else:
    raise SystemExit('Student still has direct packet socket access')
with socket.socket(socket.AF_UNIX) as connection:
    connection.settimeout(8)
    connection.connect('/run/cisco-lab-capture.sock')
    assert connection.recv(1) == b'\\0', 'LAB capture rejected'
    header = b''
    while len(header) < 24:
        chunk = connection.recv(24 - len(header))
        assert chunk, 'Missing capture header'
        header += chunk
    magic = header[:4].hex()
    byte_order = {'d4c3b2a1': '<', '4d3cb2a1': '<',
                  'a1b2c3d4': '>', 'a1b23c4d': '>'}[magic]
    assert struct.unpack(byte_order + 'I', header[20:])[0] == 1
"""
    runtime = f'/run/user/{uid}'
    run('runuser', '-u', username, '--', 'env', f'XDG_RUNTIME_DIR={runtime}',
        f'DBUS_SESSION_BUS_ADDRESS=unix:path={runtime}/bus',
        'systemd-run', '--user', '--wait', '--pipe', '--quiet', '--collect',
        '/usr/bin/python3', '-IB', '-c', script)


def activate(payload):
    if run('dpkg-divert', '--truename', str(DUMPCAP)).stdout.strip() != str(DUMPCAP):
        raise RuntimeError('Unexpected dumpcap diversion')
    if run('dpkg-statoverride', '--list', str(DUMPCAP), check=False).stdout.strip():
        raise RuntimeError('Unexpected dumpcap statoverride')
    run('systemctl', 'is-active', SOCKET_UNIT)
    if run('systemctl', 'list-units', '--state=running', '--no-legend',
           'cisco-lab-capture@*.service').stdout.strip():
        raise RuntimeError('A LAB capture is still running')

    processes = run('ps', '-C', 'dumpcap', '-o', 'stat=', check=False)
    if processes.returncode not in (0, 1) or any(
        not status.startswith('Z') for status in processes.stdout.split()
    ):
        raise RuntimeError('Could not exclude a running direct capture')
    enabled = run('systemctl', 'is-enabled', SOCKET_UNIT, check=False).stdout.strip()
    selection = debconf(f'GET {QUESTION}')
    if enabled not in ('enabled', 'disabled') or selection not in ('true', 'false'):
        raise RuntimeError('Unexpected socket or debconf state')
    dumpcap = DUMPCAP.lstat()
    starter = STARTER.lstat()
    if not stat.S_ISREG(dumpcap.st_mode) or not stat.S_ISREG(starter.st_mode):
        raise RuntimeError('Expected regular files for dumpcap and the starter')
    state = dict(enabled=enabled == 'enabled', debconf=selection,
                 dumpcap_uid=dumpcap.st_uid, dumpcap_gid=dumpcap.st_gid,
                 dumpcap_mode=stat.S_IMODE(dumpcap.st_mode),
                 capabilities=base64.b64encode(capabilities()).decode(),
                 starter=(starter.st_uid, starter.st_gid, stat.S_IMODE(starter.st_mode)))
    backup = Path(tempfile.mkdtemp(prefix='activation-', dir=BACKUP_ROOT))
    (backup / 'state.json').write_text(json.dumps(state, indent=2))
    (backup / 'previous.desktop').write_bytes(STARTER.read_bytes())
    (backup / 'activate.py').write_text(payload['script'])
    (backup / 'candidate.desktop').write_text(payload['desktop'])
    print(f'Sicherung: {backup}', flush=True)

    try:
        debconf(f'SET {QUESTION} false')
        set_capabilities(b'')
        os.chown(DUMPCAP, 0, 0)
        os.chmod(DUMPCAP, 0o755)
        if capabilities() or debconf(f'GET {QUESTION}') != 'false':
            raise RuntimeError('Direct dumpcap privileges are still configured')
        write_starter(payload['desktop'].encode(), 0, 0, 0o644)
        run('systemctl', 'enable', SOCKET_UNIT)
        run('systemctl', 'is-enabled', SOCKET_UNIT)
        probe(payload['uid'], payload['username'])
    except BaseException:
        restore(backup)
        raise
    print('Direkter Paketsocket verweigert; LAB-Mitschnitt und Boot-Aktivierung geprüft.')


if __name__ == '__main__':
    if os.geteuid() != 0 or socket.gethostname().lower() != 'lab-08a':
        raise SystemExit('Nur als root auf LAB-08A ausführen.')
    if len(sys.argv) == 3 and sys.argv[1] == '--restore':
        restore(Path(sys.argv[2]))
    else:
        activate(json.load(sys.stdin))
