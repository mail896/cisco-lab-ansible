"""Kernel peer credentials plus active local logind sessions, not caller JSON."""
from dataclasses import dataclass
import grp
import os
import re
from pathlib import Path
import socket
import struct
import subprocess


class Denied(PermissionError):
    pass


@dataclass(frozen=True)
class Peer:
    pid: int
    uid: int
    gid: int
    groups: frozenset
    session_id: str | None = None


def peer_credentials(connection, proc=Path('/proc')):
    pid, uid, gid = struct.unpack('3i', connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
    text = (proc / str(pid) / 'status').read_text()
    fields = dict(line.split(':', 1) for line in text.splitlines() if ':' in line)
    # A process that changed UID since connecting must reconnect.
    uids = [int(n) for n in fields['Uid'].split()]
    if uids[0] != uid or uids[1] != uid:
        raise Denied('Prozessidentitaet hat sich geaendert; Dialog neu starten.')
    groups = frozenset(int(n) for n in fields['Groups'].split()) | {gid}
    cgroup=(proc / str(pid) / 'cgroup').read_text()
    match=re.search(r'/user\.slice/user-\d+\.slice/session-([A-Za-z0-9_]+)\.scope(?:/|$)',cgroup,re.MULTILINE)
    return Peer(pid, uid, gid, groups, match.group(1) if match else None)


def run(args):
    return subprocess.run(args, capture_output=True, text=True, check=True, timeout=5).stdout


def local_sessions(command=run):
    result = []
    for row in command(['/usr/bin/loginctl', 'list-sessions', '--no-legend']).splitlines():
        sid = row.split()[0]
        text = command(['/usr/bin/loginctl', 'show-session', sid,
                        '-p', 'User', '-p', 'Name', '-p', 'Type', '-p', 'Class',
                        '-p', 'Remote', '-p', 'Active', '-p', 'Seat'])
        fields = dict(line.split('=', 1) for line in text.splitlines() if '=' in line)
        if (fields.get('Type') in ('x11', 'wayland') and fields.get('Class') == 'user'
                and fields.get('Remote') == 'no' and fields.get('Active') == 'yes'
                and fields.get('Seat') == 'seat0'):
            result.append({'id': sid, 'uid': int(fields['User']), 'name': fields['Name']})
    return result


def authorize(peer, gid, sessions):
    if peer.uid == 0 or gid not in peer.groups:
        raise Denied('Nur angemeldete Mitglieder der IServ-LAB-Gruppe duerfen das Labornetz aendern.')
    matches = [s for s in sessions if s['uid'] == peer.uid]
    if len(matches) != 1:
        raise Denied('Keine eindeutige aktive lokale LAB-Sitzung gefunden.')
    if peer.session_id is not None and peer.session_id != matches[0]['id']:
        raise Denied('Der aufrufende Prozess gehoert nicht zur aktiven lokalen Sitzung.')
    return matches[0]


def group_id(name):
    return grp.getgrnam(name).gr_gid
