#!/usr/bin/python3
"""Keep the packaged KDE settings launchers inaccessible to the LAB group."""
import argparse
import os
from pathlib import Path
import stat
import subprocess


BINARIES = (Path('/usr/bin/systemsettings'), Path('/usr/bin/kcmshell6'))
BASE_ACL = {'user::rwx', 'group::r-x', 'other::r-x'}


def apply_acl(gid, check=False):
    if os.geteuid() != 0 or not 0 < gid < 2**32 - 1:
        raise ValueError('Root und eine gültige LAB-Gruppen-ID sind erforderlich.')
    entry = f'group:{gid}:---'
    managed_acl = BASE_ACL | {entry, 'mask::r-x'}
    changes = []
    for path in BINARIES:
        try:
            info = path.lstat()
        except FileNotFoundError:
            if check:
                raise
            continue  # A package may have just been removed.
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_gid != 0
                or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o755):
            raise ValueError(f'Unerwarteter Dateityp oder Rechte: {path}')
        output = subprocess.check_output(
            ['/usr/bin/getfacl', '-cpn', '--', str(path)], text=True, timeout=5,
        )
        acl = set(output.split())
        if acl == BASE_ACL:
            changes.append(path)
        elif acl != managed_acl:
            raise ValueError(f'Vorhandene fremde ACL zuerst prüfen: {path}')
    if not check and changes:
        subprocess.run(
            ['/usr/bin/setfacl', '-m', entry, '--', *map(str, changes)],
            check=True, timeout=10,
        )


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('gid', type=int)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    apply_acl(args.gid, args.check)
