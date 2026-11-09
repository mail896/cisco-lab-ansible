"""Shared, demand-driven ICMP status for the 17 fixed LAB computers."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import secrets
import socket
import struct
import threading
import time

from dashboard.deploy import LAB_TARGETS
from maintenance.teacher_wake import ADDRESS as TEACHER_ADDRESS

TARGETS = {**LAB_TARGETS, 'LAB-Teacher': TEACHER_ADDRESS}
INTERVAL = 30
TIMEOUT = 0.7


def checksum(data):
    if len(data) % 2:
        data += b'\0'
    total = sum(struct.unpack('!' + 'H' * (len(data) // 2), data))
    while total >> 16:
        total = (total & 0xffff) + (total >> 16)
    return (~total) & 0xffff


def ping(address):
    if address not in TARGETS.values():
        raise ValueError('Kein festes LAB-Ziel.')
    nonce = secrets.token_bytes(16)
    sequence = secrets.randbelow(65536)
    packet = struct.pack('!BBHHH', 8, 0, 0, 0, sequence) + nonce
    packet = packet[:2] + struct.pack('!H', checksum(packet)) + packet[4:]
    start = time.monotonic()
    try:
        # Linux ping sockets need no root, raw socket or external process.
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_ICMP) as sock:
            sock.settimeout(TIMEOUT)
            sock.sendto(packet, (address, 0))
            deadline = start + TIMEOUT
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return dict(state='unreachable', rtt_ms=None)
                sock.settimeout(remaining)
                reply, source = sock.recvfrom(256)
                if (source[0] == address and len(reply) == len(packet)
                        and reply[:2] == b'\0\0' and reply[6:8] == packet[6:8]
                        and reply[8:] == nonce):
                    return dict(state='online', rtt_ms=round((time.monotonic() - start) * 1000, 1))
    except TimeoutError:
        return dict(state='unreachable', rtt_ms=None)
    except OSError:
        # Local permission/routing errors do not prove that a PC is off.
        return dict(state='unknown', rtt_ms=None)


class Presence:
    def __init__(self, probe=ping, clock=time.monotonic, wall=time.time):
        self.probe, self.clock, self.wall = probe, clock, wall
        self.lock = threading.Lock()
        self.devices = {host: dict(state='unknown', rtt_ms=None) for host in TARGETS}
        self.checked = None
        self.checked_at = None
        self.refreshing = False

    def snapshot(self):
        with self.lock:
            if not self.refreshing and (self.checked is None or self.clock() - self.checked >= INTERVAL):
                self.refreshing = True
                threading.Thread(target=self._refresh, daemon=True).start()
            age = None if self.checked is None else max(0, self.clock() - self.checked)
            return dict(devices={host: dict(row) for host, row in self.devices.items()},
                        checked_at=self.checked_at, age_seconds=None if age is None else round(age, 1),
                        interval_seconds=INTERVAL, refreshing=self.refreshing)

    def _refresh(self):
        results = {}
        try:
            with ThreadPoolExecutor(max_workers=4, thread_name_prefix='lab-ping') as pool:
                pending = {host: pool.submit(self.probe, address) for host, address in TARGETS.items()}
                for host, future in pending.items():
                    try:
                        results[host] = future.result()
                    except Exception:
                        results[host] = dict(state='unknown', rtt_ms=None)
            with self.lock:
                self.devices = results
                self.checked = self.clock()
                self.checked_at = datetime.fromtimestamp(self.wall(), timezone.utc).isoformat(timespec='seconds')
        finally:
            with self.lock:
                self.refreshing = False
