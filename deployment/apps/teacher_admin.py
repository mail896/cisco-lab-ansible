"""Operator-only SSH administration of the fixed Teacher host, separate from APP runtime."""
import argparse
import os
from pathlib import Path
import shlex
import stat
import subprocess

ROOT = Path(__file__).resolve().parents[2]
ADDRESS = '203.0.113.100'


def connection(command):
    directory = ROOT / 'local-private/teacher-admin'
    for path in (directory / 'id_ed25519', directory / 'known_hosts'):
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError('Privater Teacher-Administrationsschlüssel oder Host-Pin unsicher.')
    return ['/usr/bin/ssh', '-T', '-F', '/dev/null', '-o', 'BatchMode=yes',
            '-o', 'IdentitiesOnly=yes', '-o', 'IdentityAgent=none', '-o', 'ConnectTimeout=5',
            '-o', 'StrictHostKeyChecking=yes', '-o', 'GlobalKnownHostsFile=/dev/null',
            '-o', 'UserKnownHostsFile=' + str(directory / 'known_hosts'),
            '-i', str(directory / 'id_ed25519'), 'root@' + ADDRESS, shlex.join(command)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ['--'] else args.command
    if not command:
        parser.error('Teacher-Befehl erforderlich.')
    raise SystemExit(subprocess.run(connection(command)).returncode)


if __name__ == '__main__':
    main()
