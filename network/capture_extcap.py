#!/usr/bin/python3 -I
"""Unprivileged Wireshark adapter for the local LAB capture service."""
import argparse
import errno
import os
import select
import socket
import stat
import struct
import sys
import time

INTERFACE = 'cisco-lab-usb'
SOCKET = '/run/cisco-lab-capture.sock'


def open_fifo(path):
    deadline = time.monotonic() + 5
    while True:
        try:
            fd = os.open(path, os.O_WRONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
        except OSError as error:
            if error.errno != errno.ENXIO or time.monotonic() >= deadline:
                raise
            time.sleep(0.05)
        else:
            if not stat.S_ISFIFO(os.fstat(fd).st_mode):
                os.close(fd)
                raise ValueError('Wireshark-Mitschnittziel ist keine Pipe.')
            os.set_blocking(fd, True)
            return os.fdopen(fd, 'wb', buffering=0)


def capture(path):
    with open_fifo(path) as target, socket.socket(socket.AF_UNIX) as connection:
        connection.settimeout(8)
        connection.connect(SOCKET)
        _, uid, _ = struct.unpack('3i', connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
        if uid != 0:
            raise PermissionError('Der LAB-Mitschnittdienst ist nicht vertrauenswürdig.')
        if connection.recv(1) != b'\0':
            raise PermissionError('Kein Mitschnittzugriff. LAB-Anmeldung und USB-Adapter prüfen.')
        connection.settimeout(None)
        poll = select.poll()
        poll.register(connection, select.POLLIN | select.POLLHUP | select.POLLERR)
        poll.register(target, select.POLLHUP | select.POLLERR)
        while True:
            ready = dict(poll.poll())
            if target.fileno() in ready:
                return
            data = memoryview(connection.recv(65536))
            if not data:
                return
            while data:
                data = data[target.write(data):]


def main():
    parser = argparse.ArgumentParser()
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument('--extcap-interfaces', action='store_true')
    action.add_argument('--extcap-dlts', action='store_true')
    action.add_argument('--extcap-config', action='store_true')
    action.add_argument('--capture', action='store_true')
    parser.add_argument('--extcap-version')
    parser.add_argument('--extcap-interface', choices=[INTERFACE])
    parser.add_argument('--extcap-capture-filter', default='')
    parser.add_argument('--fifo')
    args = parser.parse_args()
    if args.extcap_interfaces:
        print('interface {value=cisco-lab-usb}{display=USB-Labornetz}')
    elif args.extcap_interface != INTERFACE:
        parser.error('Unbekannte Mitschnittquelle.')
    elif args.extcap_dlts:
        print('dlt {number=1}{name=EN10MB}{display=Ethernet}')
    elif args.capture:
        if not args.fifo:
            parser.error('Wireshark-Pipe fehlt.')
        if args.extcap_capture_filter:
            parser.error('Bitte den Anzeigefilter in Wireshark verwenden.')
        capture(args.fifo)


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError) as error:
        print(error, file=sys.stderr)
        raise SystemExit(1)
