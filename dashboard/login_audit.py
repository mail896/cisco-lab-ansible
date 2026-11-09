"""Private daily sign-in records containing verified identity, never tokens."""
from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
from itertools import islice
import os
from pathlib import Path
import re
import stat
import threading
import time

RETENTION_DAYS = 90
MAX_FILE_BYTES = 8 * 1024 * 1024
REASONS = {'signed_in', 'not_collegium', 'provider_denied', 'verification_failed', 'provider_unavailable'}


class LoginAudit:
    def __init__(self, directory, clock=time.time):
        self.directory, self.clock = Path(directory), clock
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = self.directory.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError('Anmeldeprotokoll benötigt ein privates LAB-Verzeichnis.')
        self.lock = threading.Lock()
        self.purged_day = None

    def now(self):
        return datetime.fromtimestamp(self.clock(), timezone.utc)

    @staticmethod
    def verify(fd):
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or info.st_mode & 0o077 or info.st_size > MAX_FILE_BYTES):
            raise ValueError('Anmeldeprotokoll ist nicht privat oder zu groß.')

    def files(self):
        cutoff = (self.now().date() - timedelta(days=RETENTION_DAYS - 1)).isoformat()
        paths = sorted((path for path in self.directory.iterdir()
                        if re.fullmatch(r'\d{4}-\d{2}-\d{2}\.jsonl', path.name)), reverse=True)
        return paths, cutoff

    def append(self, outcome, reason, user=None):
        if outcome not in {'success', 'denied', 'failed'} or reason not in REASONS:
            raise ValueError('Unbekanntes Anmeldeereignis.')
        user = user or {}
        row = dict(time=self.now().isoformat(timespec='seconds'), outcome=outcome, reason=reason,
                   account=user.get('account'), name=user.get('name'), role=user.get('role'))
        line = (json.dumps(row, ensure_ascii=False, separators=(',', ':')) + '\n').encode()
        if len(line) > 2048:
            raise ValueError('Anmeldeereignis ist zu groß.')
        with self.lock:
            today = self.now().date().isoformat()
            fd = os.open(self.directory / (today + '.jsonl'), os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
            try:
                self.verify(fd)
                if os.write(fd, line) != len(line):
                    raise OSError('Anmeldeprotokoll unvollständig geschrieben.')
                os.fsync(fd)
            finally:
                os.close(fd)
            if self.purged_day != today:
                paths, cutoff = self.files()
                for path in paths:
                    if path.stem < cutoff:
                        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
                        try:
                            self.verify(fd)
                        finally:
                            os.close(fd)
                        path.unlink()
                self.purged_day = today

    @staticmethod
    def reverse_lines(source):
        position = source.seek(0, os.SEEK_END)
        pending = b''
        while position:
            amount = min(position, 65536)
            position -= amount
            source.seek(position)
            parts = (source.read(amount) + pending).split(b'\n')
            pending = parts[0]
            for line in reversed(parts[1:]):
                if line:
                    yield line.decode('utf-8')
        if pending:
            yield pending.decode('utf-8')

    def iter_records(self):
        with self.lock:
            paths, cutoff = self.files()
            for path in paths:
                if path.stem < cutoff:
                    continue
                fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
                try:
                    self.verify(fd)
                    with os.fdopen(fd, 'rb') as source:
                        fd = None
                        for line in self.reverse_lines(source):
                            row = json.loads(line)
                            if (not isinstance(row, dict) or set(row) != {'time', 'outcome', 'reason', 'account', 'name', 'role'}
                                    or row['outcome'] not in {'success', 'denied', 'failed'} or row['reason'] not in REASONS):
                                raise ValueError('Anmeldeprotokoll enthält einen ungültigen Eintrag.')
                            yield row
                finally:
                    if fd is not None:
                        os.close(fd)

    def records(self, limit=200):
        with closing(self.iter_records()) as records:
            return list(islice(records, limit))
