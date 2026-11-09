"""One socket-activated Wireshark capture on the verified LAB adapter."""
import os
from pathlib import Path
import select
import socket
import subprocess
import sys

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from network.broker import auth
from network.broker.backend import Backend
from network.broker.service import load_config


def capture_command(identity):
    interface = Backend(identity).resolved()['lab']['interface']
    return ['/var/cache/cisco-lab-capture/dumpcap', '-q', '-i', interface, '-P',
            '-a', 'duration:1800', '-w', '-']


def relay(connection, command):
    with subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                          env={'PATH': '/usr/bin:/usr/sbin', 'LC_ALL': 'C'}) as capture:
        try:
            connection.sendall(b'\0')
            while True:
                ready, _, _ = select.select([connection, capture.stdout], [], [])
                if connection in ready:
                    return  # Disconnect or unexpected input.
                data = os.read(capture.stdout.fileno(), 65536)
                if not data:
                    if capture.wait(timeout=3):
                        raise RuntimeError('Mitschnittprozess fehlgeschlagen.')
                    return
                connection.sendall(data)
        finally:
            if capture.poll() is None:
                capture.terminate()
                try:
                    capture.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    capture.kill()


def main():
    with socket.socket(fileno=os.dup(sys.stdin.fileno())) as connection:
        connection.settimeout(5)
        try:
            config = load_config()
            gid = auth.group_id(config['group'])
            if gid <= 0:
                raise PermissionError('Ungültige LAB-Gruppe.')
            auth.authorize(auth.peer_credentials(connection), gid, auth.local_sessions())
            command = capture_command(config['identity'])
        except (ValueError, OSError, KeyError, RuntimeError, subprocess.SubprocessError) as error:
            print(f'LAB capture denied: {error}', file=sys.stderr)
            connection.sendall(b'\1')
            return 1
        try:
            relay(connection, command)
        except (BrokenPipeError, ConnectionResetError):
            return 0
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
