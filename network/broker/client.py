"""Unprivileged JSON client; no sudo, nmcli, identity arguments or TCP fallback."""
import json
import socket
import struct
from network.broker.protocol import receive, send
from network.security.lab_request import decode_request

SOCKET = '/run/cisco-lab-network/control.sock'


def request(payload, path=SOCKET, timeout=120):
    # Bound user input before connecting. Real validation is always server-side.
    payload = decode_request(json.dumps(payload))
    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as connection:
        connection.settimeout(timeout)
        connection.connect(path)
        _,uid,_=struct.unpack('3i',connection.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12))
        if uid != 0:
            raise PermissionError('Der Labornetz-Dienst laeuft nicht als vertrauenswuerdiger Systemdienst.')
        send(connection,payload)
        response = receive(connection,limit=65536,decoder=json.loads)
    if not isinstance(response,dict) or type(response.get('ok')) is not bool:
        raise ValueError('Ungueltige Antwort des Labornetz-Dienstes.')
    if not response['ok']:
        raise ValueError(response.get('error','Labornetz-Vorgang fehlgeschlagen.'))
    return response
