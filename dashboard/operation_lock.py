"""Serialize GUI and scheduled management across processes on LAB-Teacher."""
import fcntl
import os
from pathlib import Path
import stat
import threading


class OperationLock:
    def __init__(self, path):
        path = Path(path)
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        self.file = os.fdopen(descriptor, 'a+b')
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            self.file.close()
            raise ValueError('Verwaltungssperre ist nicht privat.')
        self.guard = threading.Lock()

    def __del__(self):
        file = getattr(self, 'file', None)
        if file is not None:
            file.close()

    def acquire(self, blocking=True):
        if not self.guard.acquire(blocking=blocking):
            return False
        try:
            fcntl.flock(self.file, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
            return True
        except BlockingIOError:
            self.guard.release()
            return False
        except BaseException:
            self.guard.release()
            raise

    def release(self):
        if not self.guard.locked():
            raise RuntimeError('Verwaltungssperre ist nicht belegt.')
        try:
            fcntl.flock(self.file, fcntl.LOCK_UN)
        finally:
            self.guard.release()

    def locked(self):
        if not self.acquire(blocking=False):
            return True
        self.release()
        return False
