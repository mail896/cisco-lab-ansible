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

from dashboard.deploy import LAB_TARGETS, PILOTS, ROOT
from maintenance.preview import BERLIN

TARGETS = LAB_TARGETS
ACTIONS = {'wake': 'Aufwecken', 'logout': 'Schüler abmelden',
           'reboot': 'Freie Clients neu starten', 'poweroff': 'Freie Clients herunterfahren',
           'demo': 'Sammelkonto cisco anmelden', 'demo-end': 'Sammelkonto cisco abmelden'}
SCOPES = {'pilot-a': ['LAB-08A'], 'pilot-b': ['LAB-08B'],
          'pilots': list(PILOTS), 'room': list(TARGETS),
          **{host:[host] for host in TARGETS}}
SOURCE = Path(__file__).with_name('room_action.py')
WOL_INVENTORY = ROOT / 'local-private' / 'wol-inventory.json'


class RoomActions:
    def __init__(self, directory, deploys):
        self.directory = directory
        self.deploys = deploys
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        for run in self.history():
            if run['status'] == 'running':
                run['status'] = 'interrupted'
                self.save(run)

    def save(self, run):
        path = self.directory / (run['id'] + '.json')
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(run, ensure_ascii=False))
        temporary.replace(path)

    def history(self):
        runs = [json.loads(path.read_text()) for path in self.directory.glob('*.json')]
        return sorted(runs, key=lambda run: run['started'], reverse=True)[:20]

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


    def start(self, action, scope, confirmed):
        if action not in ACTIONS or scope not in SCOPES or confirmed is not True:
            raise ValueError('Aktion, Zielgruppe und Bestätigung erforderlich.')
        if not self.deploys.lock.acquire(blocking=False):
            return None
        try:
            wake = (self.macs(), self.wake_route()) if action == 'wake' else None
            run = dict(id=uuid.uuid4().hex, action=action, scope=scope, status='running', results={},
                       started=datetime.now(BERLIN).isoformat(timespec='seconds'))
            self.save(run)
            threading.Thread(target=self.execute, args=(run, wake), daemon=True).start()
            return dict(run)
        except BaseException:
            self.deploys.lock.release()
            raise

    def on_host(self, host, action, wake, *, maintenance_owner=False):
        now = datetime.now(BERLIN)
        if (not maintenance_owner and host == 'LAB-08B' and action != 'wake' and now.weekday() < 5
                and (17, 45) <= (now.hour, now.minute) < (20, 0)):
            return 'Übersprungen: Für automatische Wartung reserviert.'
        try:
            if action == 'wake':
                macs, route = wake
                return self.wake(macs[host], route)
            known = self.deploys.known_hosts_for(host)
            if not known or not known.is_file():
                return 'Fehler: Verifizierte SSH-Hostdatei fehlt.'
            loader = "import sys; source = sys.stdin.read(); exec(compile(source, '<room-action>', 'exec'))"
            remote = shlex.join(['sudo', '-n', '/usr/bin/python3', '-IB', '-c', loader, host, action])
            command = ['/usr/bin/ssh', '-T', '-F', '/dev/null', '-o', 'BatchMode=yes',
                       '-o', 'IdentitiesOnly=yes', '-o', 'StrictHostKeyChecking=yes',
                       '-o', 'GlobalKnownHostsFile=/dev/null', '-o', f'UserKnownHostsFile={known}',
                       '-o', 'ConnectTimeout=5', '-i', str(Path.home()/'.ssh/id_ed25519'),
                       f'administrator@{TARGETS[host]}', remote]
            result = subprocess.run(command, input=SOURCE.read_text(), capture_output=True,
                                    text=True, timeout=75)
        except (OSError, subprocess.SubprocessError) as error:
            return f'Fehler: {error}'
        output = result.stdout.strip()
        queued = 'Systemaktion eingeplant. Sitzung wird vor Ausführung erneut geprüft.'
        if action in ('reboot', 'poweroff') and queued in output.splitlines():
            return queued
        if result.returncode:
            detail = (result.stderr.strip() or result.stdout.strip()).splitlines()
            return 'Fehler: ' + (detail[-1][:300] if detail else 'Aktion nicht bestätigt.')
        return output or 'Anforderung übermittelt.'

    def execute(self, run, wake):
        try:
            targets = SCOPES[run['scope']]
            workers = 8 if run['action'] in ('demo','demo-end') else 4
            with ThreadPoolExecutor(max_workers=workers) as pool:
                jobs = {pool.submit(self.on_host,host,run['action'],wake):host for host in targets}
                for job in as_completed(jobs):
                    run['results'][jobs[job]] = job.result()
                    self.save(run)
            run['status'] = 'finished'
        finally:
            try:
                if run['status'] == 'running':
                    run['status'] = 'interrupted'
                self.save(run)
            finally:
                self.deploys.lock.release()
