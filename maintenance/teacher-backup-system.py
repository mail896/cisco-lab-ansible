#!/usr/bin/python3 -IB
"""Export only the fixed LAB-Teacher system configuration to the encrypted-backup process."""
import os
from pathlib import Path
import socket
import subprocess
import sys

if len(sys.argv) != 1 or os.geteuid() != 0 or socket.gethostname() != 'LAB-Teacher':
    raise SystemExit('Nur ohne Argumente als root auf LAB-Teacher.')

paths = [
    'etc/ssl/cisco-lab',
    'etc/nginx/conf.d/cisco-lab-dashboard.conf',
    'etc/veyon',
    'etc/xdg/Veyon Solutions/Veyon.conf',
    'etc/cisco-lab',
    'etc/systemd/system',
    'etc/fstab',
    'etc/hostname',
    'etc/hosts',
    'etc/sudoers.d/cisco-lab-system-backup',
    'usr/local/sbin/cisco-lab-backup-system',
]
paths = [path for path in paths if (Path('/')/path).exists() or (Path('/')/path).is_symlink()]
subprocess.run(['/usr/bin/tar', '--acls', '--xattrs', '-czf', '-', '-C', '/', '--', *paths], check=True)
