"""List local IServ home containers without descending into their contents."""
import json
from pathlib import Path
import re
import socket
import subprocess
import sys
from time import time

ROOT = Path('/var/lib/iserv/client/home')
LOCAL_FILESYSTEMS = {'ext4', 'xfs', 'btrfs', 'f2fs', 'ext3', 'ext2'}


def mounts():
    for line in Path('/proc/self/mountinfo').read_text().splitlines():
        before, after = line.split(' - ', 1)
        path = re.sub(r'\\([0-7]{3})', lambda match: chr(int(match[1], 8)), before.split()[4])
        yield Path(path), after.split()[0]


def main(host):
    if socket.gethostname() != host:
        raise SystemExit('Geräteidentität stimmt nicht mit dem Inventar überein.')

    table = dict(mounts())
    parent = max((p for p in table if ROOT.is_relative_to(p)), key=lambda p: len(p.parts))
    if table[parent] not in LOCAL_FILESYSTEMS:
        return {'status': 'Profilwurzel liegt nicht auf einem bekannten lokalen Dateisystem.', 'profiles': []}
    if not ROOT.is_dir() or ROOT.resolve() != ROOT:
        return {'status': 'IServ-Profilwurzel fehlt oder ist umgeleitet.', 'profiles': []}

    local = [line.split(':') for line in Path('/etc/passwd').read_text().splitlines() if ':' in line]
    protected = {row[0] for row in local} | {'administrator', 'jobadmin', 'cisco'}
    local_uids = {int(row[2]) for row in local}
    sessions = subprocess.check_output(['loginctl', 'list-sessions', '--no-legend'], text=True, timeout=8)
    active_uids = {int(line.split()[1]) for line in sessions.splitlines() if line.strip()}
    profiles = []
    for path in sorted(ROOT.iterdir()):
        if any(point.is_relative_to(path) for point in table):
            profiles.append({
                'directory': path.name,
                'directory_mtime_days': None,
                'protected_because': 'Eingebundenes Profil oder Unter-Mount',
            })
            continue
        if path.is_symlink() or not path.is_dir():
            continue
        info = path.stat(follow_symlinks=False)
        if path.name in protected or info.st_uid in local_uids:
            reason = 'Lokales Konto oder lokaler Besitzer'
        elif info.st_uid in active_uids:
            reason = 'Sitzung aktiv'
        else:
            reason = 'Letzte Nutzung nicht nachgewiesen'
        profiles.append({
            'directory': path.name,
            'directory_mtime_days': max(0, int((time() - info.st_mtime) / 86400)),
            'protected_because': reason,
        })

    return {'status': 'Nur Vorschau. Keine Löschfreigabe.', 'profiles': profiles}


if __name__ == '__main__':
    print(json.dumps(main(sys.argv[1]), ensure_ascii=False))
