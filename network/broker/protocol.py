"""One bounded newline-delimited JSON request/response per Unix connection."""
import json
from network.security.lab_request import LIMIT, decode_request


def receive(connection, limit=LIMIT, decoder=decode_request):
    chunks = bytearray()
    while True:
        data = connection.recv(min(4096, limit + 1 - len(chunks)))
        if not data:
            raise ValueError('Unvollstaendige Anfrage.')
        chunks.extend(data)
        if len(chunks) > limit:
            raise ValueError('Anfrage ist zu gross.')
        if b'\n' in data:
            line, remainder = bytes(chunks).split(b'\n', 1)
            if remainder.strip():
                raise ValueError('Nur eine Anfrage pro Verbindung erlaubt.')
            return decoder(line.decode('utf-8'))


def send(connection, value):
    connection.sendall(json.dumps(value, ensure_ascii=True).encode() + b'\n')
