#!/usr/bin/python3
"""Root-only file snapshots; restore file state, not packages or user homes."""
import base64
import datetime
import json
import os
from pathlib import Path
import shutil
import sys


def snapshot(paths, base):
    directory = Path(base) / datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
    directory.mkdir(parents=True, mode=0o700)
    entries = []
    for name in paths:
        path = Path(name)
        entry = {'path': name, 'exists': path.exists() or path.is_symlink()}
        if entry['exists']:
            stat = path.lstat()
            entry.update(uid=stat.st_uid, gid=stat.st_gid, mode=stat.st_mode & 0o7777)
            if path.is_symlink():
                entry['symlink'] = os.readlink(path)
            elif path.is_file():
                entry['content'] = base64.b64encode(path.read_bytes()).decode()
            else:
                raise ValueError(f'Not a regular file or symlink: {name}')
        entries.append(entry)
    (directory / 'files.json').write_text(json.dumps(entries, indent=2))
    (directory / 'files.json').chmod(0o600)
    return str(directory)


def restore(directory):
    for entry in json.loads((Path(directory) / 'files.json').read_text()):
        path = Path(entry['path'])
        if path.is_dir() and not path.is_symlink():
            raise ValueError(f'Refuse to replace directory: {path}')
        if path.exists() or path.is_symlink():
            path.unlink()
        if not entry['exists']:
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        if 'symlink' in entry:
            path.symlink_to(entry['symlink'])
        else:
            path.write_bytes(base64.b64decode(entry['content']))
            path.chmod(entry['mode'])
        os.chown(path, entry['uid'], entry['gid'], follow_symlinks=False)


if __name__ == '__main__':
    if os.geteuid() != 0:
        raise SystemExit('Root required')
    if len(sys.argv) == 3 and sys.argv[1] == '--restore':
        restore(sys.argv[2])
    else:
        print(snapshot(json.load(sys.stdin), '/var/backups/cisco-lab/block03'))
