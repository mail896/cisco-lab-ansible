"""Send a bounded APP WOL request and verify the Teacher SSH identity."""
import argparse
import base64
import hashlib
import re
import socket
import subprocess
import time

from dashboard.admission import read_settings
from dashboard.deploy import ROOT
from dashboard.room import RoomActions

CONFIG = ROOT / 'local-private/teacher-wake.json'
ADDRESS = '203.0.113.100'
FINGERPRINT = re.compile(r'SHA256:[A-Za-z0-9+/]{43}\Z')
MAC = re.compile(r'(?:[0-9a-f]{2}:){5}[0-9a-f]{2}\Z')


def configuration():
    data = read_settings(CONFIG)
    if (set(data) != {'version', 'host', 'address', 'school_mac', 'fingerprint'}
            or data['version'] != 1 or data['host'] != 'LAB-Teacher'
            or data['address'] != ADDRESS
            or not isinstance(data['school_mac'], str)
            or not MAC.fullmatch(data['school_mac'])
            or not isinstance(data['fingerprint'], str)
            or not FINGERPRINT.fullmatch(data['fingerprint'])):
        raise ValueError('Teacher-WOL-Konfiguration ist unvollständig oder weicht vom festen Ziel ab.')
    mac = bytes.fromhex(data['school_mac'].replace(':', ''))
    if not any(mac) or mac[0] & 1:
        raise ValueError('Teacher-Schul-MAC muss eine eindeutige Unicast-Adresse sein.')
    return data


def route():
    result = RoomActions.wake_route()
    if result != ('203.0.113.200', '203.0.113.255'):
        raise ValueError('Der geprüfte APP-Broadcastweg zum Teacher ist nicht vorhanden.')
    return result


def ssh_identity(expected):
    """Return False while SSH is absent; reject any unexpected host key."""
    result = subprocess.run(['/usr/bin/ssh-keyscan', '-T', '5', '-t', 'ed25519', ADDRESS],
                            text=True, capture_output=True, timeout=8)
    lines = [line for line in result.stdout.splitlines() if line and not line.startswith('#')]
    if not lines:
        return False
    if result.returncode or len(lines) != 1:
        raise ValueError('Teacher-SSH-Identität ist nicht eindeutig.')
    fields = lines[0].split()
    if len(fields) != 3 or fields[:2] != [ADDRESS, 'ssh-ed25519']:
        raise ValueError('Teacher-SSH-Identität weicht vom festen Ziel ab.')
    try:
        key = base64.b64decode(fields[2], validate=True)
    except ValueError as error:
        raise ValueError('Ungültiger Teacher-SSH-Hostschlüssel.') from error
    digest = 'SHA256:' + base64.b64encode(hashlib.sha256(key).digest()).decode().rstrip('=')
    if digest != expected:
        raise ValueError('Teacher-SSH-Fingerabdruck stimmt nicht mit der Bestätigung überein.')
    return True


def run(*, wake):
    if socket.gethostname().split('.')[0] != 'apps':
        raise RuntimeError('Teacher-WOL darf nur vom APP-Server ausgehen.')
    data = configuration()
    path = route()
    if ssh_identity(data['fingerprint']):
        print('LAB-Teacher ist mit bestätigter SSH-Identität bereits erreichbar. Kein WOL-Paket.')
        return
    if not wake:
        print('LAB-Teacher ist über SSH derzeit nicht erreichbar. Lesende Vorschau; kein WOL-Paket.')
        return
    RoomActions.wake(data['school_mac'], path)
    print('WOL-Paket an die bestätigte Teacher-Schul-MAC gesendet; SSH-Rückkehr wird geprüft.', flush=True)
    deadline = time.monotonic() + 600
    while time.monotonic() < deadline:
        time.sleep(10)
        if ssh_identity(data['fingerprint']):
            print('LAB-Teacher mit bestätigter SSH-Identität erreichbar. Ausschalten nicht ausgelöst.')
            return
    raise TimeoutError('Teacher nach WOL nicht per bestätigtem SSH-Hostschlüssel erreichbar.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wake', action='store_true', help='Ein WOL-Paket senden und SSH-Rückkehr prüfen')
    args = parser.parse_args()
    run(wake=args.wake)


if __name__ == '__main__':
    main()
