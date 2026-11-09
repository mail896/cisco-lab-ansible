"""Hold the LAB operation lock across a privileged runtime update."""
import fcntl
import os
from pathlib import Path
import pwd
import stat
import subprocess


def main():
    if os.geteuid() != 0:
        raise SystemExit('LAB-Runtime-Update benötigt root.')
    path = Path('/var/lib/cisco-lab/.local/state/cisco-lab-dashboard/operation.lock')
    descriptor = os.open(path, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, 'r+b') as lock:
        info = os.fstat(lock.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != pwd.getpwnam('cisco-lab').pw_uid
                or info.st_mode & 0o077):
            raise SystemExit('LAB-Verwaltungssperre ist nicht eindeutig privat.')
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit('Ein LAB-Auftrag läuft. Runtime-Update zurückgestellt.')
        result = subprocess.run(['/bin/bash', '/home/lab-admin/cisco-lab/deployment/apps/apply_fresh_fix.sh', '--locked'])
        raise SystemExit(result.returncode)


if __name__ == '__main__':
    main()
