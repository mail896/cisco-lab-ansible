"""LAB room actions with fixed inventory boundaries and persistent results."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import ipaddress
import json
from pathlib import Path
import re
import shlex
import socket
import subprocess
import threading
import uuid

from dashboard.deploy import ADMISSIONS, LAB_TARGETS, PILOTS, ROOT
from maintenance.preview import BERLIN, pinned_hosts_file
from maintenance.reservations import reserved

TARGETS = LAB_TARGETS
ACTIONS = {'wake': 'Aufwecken', 'logout': 'Schüler abmelden',
           'reboot': 'Neu starten', 'poweroff': 'Herunterfahren',
           'demo': 'Sammelkonto cisco anmelden', 'demo-end': 'Sammelkonto cisco abmelden'}
SCOPES = {'pilot-a': ['LAB-08A'], 'pilot-b': ['LAB-08B'],
          'pilots': list(PILOTS), 'room': list(TARGETS),
          'teacher': ['LAB-Teacher'], 'room-with-teacher': [*TARGETS, 'LAB-Teacher'],
          **{host:[host] for host in TARGETS}}
CAPABILITIES = {scope: [action for action in ACTIONS if all(
                    action in (['wake', 'poweroff'] if host == 'LAB-Teacher' else ACTIONS) for host in hosts)]
                for scope, hosts in SCOPES.items()}
TARGET_CAPABILITIES = {host: ['wake', 'poweroff'] if host == 'LAB-Teacher' else list(ACTIONS)
                       for host in (*TARGETS, 'LAB-Teacher')}
SOURCE = Path(__file__).with_name('room_action.py')
WOL_INVENTORY = ROOT / 'local-private' / 'wol-inventory.json'


class RoomActions:
    def __init__(self, directory, deploys, profile='teacher'):
        self.directory = directory
        self.deploys = deploys
        self.profile = profile
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        for run in self.history(limit=None):
            if run['status'] == 'running':
                run['status'] = 'interrupted'
                self.save(run)

    def save(self, run):
        path = self.directory / (run['id'] + '.json')
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(run, ensure_ascii=False))
        temporary.replace(path)

    def history(self, limit=20):
        runs = [json.loads(path.read_text()) for path in self.directory.glob('*.json')]
        return sorted(runs, key=lambda run: run['started'], reverse=True)[:limit]

    def access(self):
        if self.profile != 'apps':
            return {host: True for host in TARGETS}
        from maintenance.controller import targets
        registered = targets()
        return {host: bool(pinned_hosts_file(ADMISSIONS, host) or host in registered) for host in TARGETS}

    def capabilities(self):
        access = self.access()
        return access, {host: list(TARGET_CAPABILITIES[host]) if host == 'LAB-Teacher'
                        else list(ACTIONS) if access.get(host) else ['wake'] for host in TARGET_CAPABILITIES}

    @staticmethod
    def macs():
        inventory = json.loads(WOL_INVENTORY.read_text())
        if set(inventory) != set(TARGETS):
            raise ValueError('WOL-Inventar muss genau die 16 LAB-Clients enthalten.')
        macs = {}
        for host, address in TARGETS.items():
            record = inventory[host]
            mac = record.get('mac', '')
            if record.get('address') != address or not re.fullmatch(r'([0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}', mac):
                raise ValueError(f'WOL-Zuordnung für {host} ist ungültig.')
            macs[host] = mac.lower()
        if len(set(macs.values())) != len(TARGETS):
            raise ValueError('WOL-Inventar enthält doppelte MAC-Adressen.')
        return macs

    @staticmethod
    def wake_route():
        target = next(iter(TARGETS.values()))
        route, = json.loads(subprocess.check_output(
            ['/usr/sbin/ip', '-j', '-4', 'route', 'get', target], timeout=5))
        source = route.get('prefsrc')
        if not source or not route.get('dev') or route.get('gateway'):
            raise ValueError('Kein direkt angeschlossenes LAB-Netz für Wake-on-LAN.')
        links = json.loads(subprocess.check_output(
            ['/usr/sbin/ip', '-j', '-4', 'addr', 'show', 'dev', route['dev']], timeout=5))
        for address in links[0]['addr_info']:
            if address.get('local') == source:
                network = ipaddress.ip_interface(f"{source}/{address['prefixlen']}").network
                if all(ipaddress.ip_address(target) in network for target in TARGETS.values()):
                    return source, str(network.broadcast_address)
        raise ValueError('LAB-Ziele und Broadcast-Adresse passen nicht zusammen.')

    @staticmethod
    def wake(mac, route):
        source, broadcast = route
        packet = b'\xff' * 6 + bytes.fromhex(mac.replace(':', '')) * 16
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.bind((source, 0))
            sock.sendto(packet, (broadcast, 9))
        return 'WOL-Paket ins Schulnetz gesendet. Startstatus nicht geprüft.'


    def start(self, action, scope=None, confirmed=False, *, targets=None):
        if not isinstance(action, str) or action not in ACTIONS or confirmed is not True:
            raise ValueError('Aktion, Geräteauswahl und Bestätigung erforderlich.')
        if targets is None:
            if not isinstance(scope, str) or scope not in SCOPES:
                raise ValueError('Eine gültige LAB-Zielgruppe ist erforderlich.')
            hosts = list(SCOPES[scope])
        else:
            if (scope is not None or not isinstance(targets, list) or not 1 <= len(targets) <= len(TARGET_CAPABILITIES)
                    or any(not isinstance(host, str) or host not in TARGET_CAPABILITIES for host in targets)
                    or len(set(targets)) != len(targets)):
                raise ValueError('Bitte 1 bis 17 verschiedene bekannte LAB-Geräte auswählen.')
            hosts = [host for host in TARGET_CAPABILITIES if host in targets]
            scope = 'selection'
        if any(action not in TARGET_CAPABILITIES[host] for host in hosts):
            raise ValueError('Für den Teacher sind hier Aufwecken und Herunterfahren verfügbar.')
        if action != 'wake' and self.profile == 'apps':
            access = self.access()
            missing = [host for host in hosts if host != 'LAB-Teacher' and not access.get(host)]
            if missing:
                raise ValueError('APP-Steuerzugang noch nicht eingerichtet: ' + ', '.join(missing) + '. Diese Clients können bisher nur aufgeweckt werden.')
        if not self.deploys.lock.acquire(blocking=False):
            return None
        try:
            wake = None
            if action == 'wake':
                macs = self.macs() if any(host in TARGETS for host in hosts) else {}
                route = self.wake_route()
                if 'LAB-Teacher' in hosts:
                    from maintenance.teacher_wake import configuration, route as teacher_route
                    macs['LAB-Teacher'] = configuration()['school_mac']
                    if teacher_route() != route:
                        raise ValueError('Teacher-Broadcastweg stimmt nicht mit dem Schulnetz überein.')
                wake = macs, route
            run = dict(id=uuid.uuid4().hex, action=action, scope=scope, targets=hosts, status='running', results={},
                       started=datetime.now(BERLIN).isoformat(timespec='seconds'))
            self.save(run)
            threading.Thread(target=self.execute, args=(run, wake), daemon=True).start()
            return dict(run)
        except BaseException:
            self.deploys.lock.release()
            raise

    def on_host(self, host, action, wake, *, maintenance_owner=False):
        if not maintenance_owner and action != 'wake' and reserved(host):
            return 'Übersprungen: Für automatische Wartung reserviert.'
        try:
            if action == 'wake':
                macs, route = wake
                return self.wake(macs[host], route)
            if host == 'LAB-Teacher':
                if action != 'poweroff':
                    return 'Fehler: Dieser Teacher-Auftrag ist nicht freigegeben.'
                from maintenance import teacher_controller
                state = teacher_controller.probe()
                if state is None:
                    return 'Fehler: LAB-Teacher ist nicht erreichbar.'
                if 'manual-poweroff' not in state.get('capabilities', []):
                    return 'Fehler: Teacher-Empfänger zuerst aktualisieren.'
                identity = uuid.uuid4().hex
                answer = teacher_controller.request(f'manual-poweroff {identity} {state["boot_id"]}')
                if answer != {'poweroff_requested': True, 'id': identity}:
                    return 'Fehler: Teacher bestätigt das Herunterfahren nicht.'
                return 'Herunterfahren eingeplant. Lokale Sitzungen und Paketläufe wurden geprüft.'
            if host not in TARGETS:
                return 'Fehler: Unbekanntes LAB-Ziel.'
            known = self.deploys.known_hosts_for(host)
            if not known or not known.is_file():
                return 'Fehler: Verifizierte SSH-Hostdatei fehlt.'
            loader = "import sys; source = sys.stdin.read(); exec(compile(source, '<room-action>', 'exec'))"
            manual_power = action in ('reboot', 'poweroff') and not maintenance_owner
            remote = shlex.join(['sudo', '-n', '/usr/bin/python3', '-IB', '-c', loader, host, action,
                                 *(['manual'] if manual_power else [])])
            command = ['/usr/bin/ssh', '-T', '-F', '/dev/null', '-o', 'BatchMode=yes',
                       '-o', 'IdentitiesOnly=yes', '-o', 'StrictHostKeyChecking=yes',
                       '-o', 'GlobalKnownHostsFile=/dev/null', '-o', f'UserKnownHostsFile={known}',
                       '-o', 'ConnectTimeout=5', '-i', str(Path.home()/'.ssh/id_ed25519'),
                       f'administrator@{TARGETS[host]}', remote]
            result = subprocess.run(command, input=SOURCE.read_text(), capture_output=True,
                                    text=True, timeout=75)
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
            return f'Fehler: {error}'
        output = result.stdout.strip()
        queued = ('Systemaktion eingeplant. Angemeldete Sitzungen werden beendet.' if manual_power else
                  'Systemaktion eingeplant. Sitzung wird vor Ausführung erneut geprüft.')
        if action in ('reboot', 'poweroff') and queued in output.splitlines():
            return queued
        if result.returncode:
            detail = (result.stderr.strip() or result.stdout.strip()).splitlines()
            return 'Fehler: ' + (detail[-1][:300] if detail else 'Aktion nicht bestätigt.')
        return output or 'Anforderung übermittelt.'

    def execute(self, run, wake):
        try:
            targets = run['targets'] if 'targets' in run else SCOPES[run['scope']]
            workers = 8 if run['action'] in ('demo','demo-end') else 4
            with ThreadPoolExecutor(max_workers=workers) as pool:
                jobs = {pool.submit(self.on_host,host,run['action'],wake):host for host in targets}
                for job in as_completed(jobs):
                    run['results'][jobs[job]] = job.result()
                    self.save(run)
            failed = sum(str(result).startswith('Fehler:') for result in run['results'].values())
            run['status'] = 'failed' if failed == len(targets) else 'partial' if failed else 'finished'
        finally:
            try:
                if run['status'] == 'running':
                    run['status'] = 'interrupted'
                self.save(run)
            finally:
                self.deploys.lock.release()
