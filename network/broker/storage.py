"""Atomic root-owned state and operation serialization; configurable for offline tests."""
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import stat
import tempfile
import time


def trusted_file(path, owner=0):
    path = Path(path)
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != owner or info.st_mode & 0o022:
        raise PermissionError('Unsichere Verwaltungsdatei: ' + str(path))
    for parent in path.parents:
        item = parent.stat()
        if item.st_uid != owner or item.st_mode & 0o022:
            raise PermissionError('Unsicherer Verwaltungspfad: ' + str(parent))
    return path.read_text()


def atomic_write(path, content, mode=0o600):
    path = Path(path)
    if path.is_symlink():
        raise PermissionError('Verweigere Symlink: ' + str(path))
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(prefix='.lab-', dir=path.parent)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, 'w') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try: os.fsync(directory)
        finally: os.close(directory)
    finally:
        if os.path.exists(name): os.unlink(name)


@contextmanager
def operation_lock(path, timeout=20):
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    start = time.monotonic()
    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() - start > timeout:
                    raise TimeoutError('Netzwerkvorgang laeuft bereits. Bitte spaeter erneut versuchen.')
                time.sleep(0.05)
        yield
    finally:
        os.close(fd)
