#!/usr/bin/python3
"""Local-only privileged LAB network broker. No TCP listener or shell API."""
# Isolated execution: python3 -I /root-owned/path/network/broker/service.py
if __package__ in (None, ''):
    import sys
    from pathlib import Path
    sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
    __package__ = 'network.broker'

import collections
import grp
import json
import logging
import os
from pathlib import Path
import pwd
import socket
import socketserver
import stat
import time

from network.broker import auth
from network.broker.backend import Backend
from network.broker.protocol import receive, send
from network.broker.storage import trusted_file, atomic_write
from network.security.lab_request import validate_identity

LOG = logging.getLogger('cisco-lab-network')
CONFIG = Path('/etc/cisco-lab/network.json')
SOCKET = Path('/run/cisco-lab-network/control.sock')


def user_message(error):
    message=str(error)
    translations={
        'Default routes and overlap with school subnets are forbidden':'Default-Routen und Adressen oder Routen ins Schulnetz sind hier nicht erlaubt.',
        'Apply needs at least one manual address':'Bitte mindestens eine manuelle IP-Adresse mit Praefix eintragen oder Zuruecksetzen waehlen.',
        'Gateway must be on a configured directly connected laboratory subnet':'Die Routeradresse muss in einem eingetragenen direkt angeschlossenen Labornetz liegen.',
        'Invalid next hop':'Ungueltige Routeradresse oder falsche IP-Version.',
        'Invalid exercise address':'Ungueltige Laboradresse.',
        'Duplicate address':'Eine IP-Adresse wurde mehrfach eingetragen.',
        'At most 16 address strings allowed':'Hoechstens 16 IP-Adressen sind erlaubt.',
        'At most 32 routes allowed':'Hoechstens 32 Routen sind erlaubt.',
        'Only apply/clear with addresses/routes is permitted':'Ungueltige Anfrage an den Labornetz-Dienst.',
    }
    if message in translations:return translations[message]
    if isinstance(error,ValueError) and ('does not appear to be' in message or 'has host bits set' in message):
        return 'Bitte gueltige IP-Adressen und Zielnetze mit passendem Praefix eingeben.'
    return message[:2000]


def load_config(path=CONFIG):
    value = json.loads(trusted_file(path))
    if set(value) != {'version','host','group','identity'} or type(value['version']) is not int or value['version'] != 1:
        raise ValueError('Ungueltige Vermittler-Konfiguration.')
    if value['host'] != socket.gethostname():
        raise ValueError('Verwaltungskonfiguration gehoert zu einem anderen Rechner.')
    if value['group'] != 'cisco.lab':
        raise ValueError('Nur die bestaetigte IServ-Gruppe cisco.lab ist vorgesehen.')
    validate_identity(value['identity'])
    return value


class Handler(socketserver.BaseRequestHandler):
    def handle(self):
        connection = self.request
        connection.settimeout(3)
        try:
            # Identity comes from SO_PEERCRED, never from the request.
            peer, session = self.server.authenticate(connection)
            self.server.rate_limit(peer.uid)
            request = receive(connection)
            if request == {'action':'status'}:
                self.server.backend.ensure_session(self.server.session_token(session),peer.uid)
                response = self.server.backend.status()
            else:
                # Validate BEFORE resetting a session or mutating anything.
                from network.security.lab_request import validate_request
                request = validate_request(request,self.server.backend.identity)
                self.server.backend.ensure_session(self.server.session_token(session),peer.uid)
                response = self.server.backend.apply(request,peer.uid)
            send(connection,response)
        except (ValueError, OSError, KeyError, TypeError, RuntimeError, TimeoutError) as error:
            LOG.warning('LAB request rejected/failed: %r',str(error))
            try: send(connection,{'ok':False,'error':user_message(error)})
            except OSError: pass


class Server(socketserver.UnixStreamServer):
    allow_reuse_address = False

    def __init__(self, path, backend, gid, authenticate=None, sessions=auth.local_sessions,
                 boot_id=None, housekeeping=True):
        self.backend, self.gid, self.sessions = backend, gid, sessions
        self.boot_id = boot_id or Path('/proc/sys/kernel/random/boot_id').read_text().strip()
        self.authenticate = authenticate or self._authenticate
        self.housekeeping = housekeeping
        self.last_sessions = self.last_heal = 0
        self.recent = {}
        self.last_tick_error = None
        super().__init__(str(path),Handler)

    def _authenticate(self, connection):
        peer = auth.peer_credentials(connection)
        if peer.uid == 0 or self.gid not in peer.groups:
            raise auth.Denied('Keine LAB-Gruppenberechtigung in dieser Sitzung.')
        if auth.group_id('cisco.lab') != self.gid:
            raise auth.Denied('LAB-Gruppenzuordnung hat sich geaendert. Bitte die Lehrkraft informieren.')
        return peer, auth.authorize(peer,self.gid,self.sessions())

    def session_token(self, session):
        return f"{self.boot_id}:{session['id']}:{session['uid']}"

    def rate_limit(self, uid):
        now = time.monotonic()
        recent = self.recent.setdefault(uid,collections.deque(maxlen=10))
        if len(recent)==10 and now-recent[0]<10:
            raise auth.Denied('Zu viele Anfragen. Bitte kurz warten.')
        recent.append(now)
        if len(self.recent)>128:
            for key in list(self.recent):
                if key != uid and (not self.recent[key] or now-self.recent[key][-1]>60):
                    self.recent.pop(key)

    def service_actions(self):
        if not self.housekeeping: return
        now = time.monotonic()
        try:
            # Heal first, then reset new local student sessions. Also checked before
            # each user request, so polling cannot later erase newly entered work.
            if now-self.last_heal >= 30:
                self.last_heal = now
                try:
                    self.backend.heal()
                    atomic_write(self.backend.state/'health.json',json.dumps({'ok':True,'time':time.time()}))
                except Exception as error:
                    atomic_write(self.backend.state/'health.json',json.dumps({'ok':False,'time':time.time(),'error':str(error)}))
                    raise
            if now-self.last_sessions >= 5:
                self.last_sessions = now
                if auth.group_id('cisco.lab') != self.gid:
                    raise auth.Denied('LAB-Gruppenzuordnung hat sich geaendert; kein Login-Reset.')
                for session in self.sessions():
                    account = pwd.getpwuid(session['uid'])
                    if self.gid in os.getgrouplist(account.pw_name,account.pw_gid):
                        self.backend.ensure_session(self.session_token(session),session['uid'])
            self.last_tick_error = None
        except Exception as error:
            message = str(error)
            if message != self.last_tick_error:
                LOG.error('LAB maintenance deferred: %s',message)
                self.last_tick_error = message


def main():
    if os.geteuid()!=0:
        raise PermissionError('Vermittlungsdienst benoetigt den Root-Systemdienst.')
    config = load_config()
    directory = SOCKET.parent
    info = directory.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise PermissionError('Unsicheres Laufzeitverzeichnis.')
    backend = Backend(config['identity'])
    # Recover school connectivity even while AD group resolution is unavailable.
    last_heal = 0
    atomic_write(backend.state/'health.json',json.dumps({'ok':False,'time':time.time()}))
    while True:
        if time.monotonic()-last_heal >= 30:
            last_heal = time.monotonic()
            try:
                backend.heal()
                atomic_write(backend.state/'health.json',json.dumps({'ok':True,'time':time.time()}))
            except Exception as error:
                atomic_write(backend.state/'health.json',json.dumps({'ok':False,'time':time.time(),'error':str(error)}))
                LOG.warning('Initial network recovery deferred: %r',str(error))
        try:
            gid = auth.group_id(config['group'])
            if gid == 0: raise ValueError('Root-Gruppe ist keine Schuelergruppe.')
            break
        except KeyError:
            LOG.warning('IServ-LAB-Gruppe noch nicht aufloesbar; Zugriff bleibt gesperrt.')
            time.sleep(5)
    # RuntimeDirectory is root-owned, group-readable; students cannot replace socket.
    directory = SOCKET.parent
    info = directory.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise PermissionError('Unsicheres Laufzeitverzeichnis.')
    if SOCKET.exists() or SOCKET.is_symlink():
        info = SOCKET.lstat()
        if not stat.S_ISSOCK(info.st_mode) or info.st_uid != 0:
            raise PermissionError('Unerwartete Datei am Socketpfad.')
        SOCKET.unlink()
    os.chown(directory,0,gid)
    os.chmod(directory,0o750)
    server = Server(SOCKET,backend,gid)
    os.chown(SOCKET,0,gid)
    os.chmod(SOCKET,0o660)
    try:
        server.serve_forever(poll_interval=1)
    finally:
        server.server_close()
        SOCKET.unlink(missing_ok=True)


if __name__=='__main__':
    logging.basicConfig(level=logging.INFO,format='%(levelname)s %(message)s')
    main()
