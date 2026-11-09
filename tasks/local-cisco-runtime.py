"""Bring known LAB runtime configurations up from their legacy boot ordering."""
from datetime import datetime, timezone
import grp
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile

SERIAL = Path('/etc/tmpfiles.d/cisco-lab-serial.conf')
CAPTURE = Path('/etc/systemd/system/cisco-lab-capture.socket')


def run(*args):
    subprocess.run(args,check=True,timeout=25)


def replace(path, text, old):
    if text==old:
        return
    info=path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid!=0:
        raise RuntimeError(f'Unerwarteter Konfigurationstyp: {path}')
    backup=Path('/var/backups/cisco-lab/local-cisco-runtime')
    backup.mkdir(mode=0o700,parents=True,exist_ok=True)
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
    shutil.copy2(path,backup/(stamp+'-'+path.name))
    with tempfile.NamedTemporaryFile(mode='w',dir=path.parent,delete=False) as file:
        name=Path(file.name)
        try:
            file.write(text)
            file.flush()
            name.chmod(stat.S_IMODE(info.st_mode))
            name.replace(path)
        finally:
            name.unlink(missing_ok=True)


def main():
    gid=grp.getgrnam('cisco.lab').gr_gid
    if SERIAL.is_file() and not SERIAL.is_symlink():
        old=SERIAL.read_text()
        text=old.replace('d /run/lock/cisco-lab-serial 2770 root cisco.lab -',
                         f'd /run/lock/cisco-lab-serial 2770 root {gid} -')
        replace(SERIAL,text,old)
        run('systemd-tmpfiles','--create',str(SERIAL))
        lock=Path('/run/lock/cisco-lab-serial').stat()
        if lock.st_gid!=gid or stat.S_IMODE(lock.st_mode)!=0o2770:
            raise RuntimeError('Konsolensperre hat nicht die erwarteten Gruppenrechte.')
    if CAPTURE.is_file() and not CAPTURE.is_symlink():
        old=CAPTURE.read_text()
        known=('ListenStream=/run/cisco-lab-capture.sock' in old.splitlines()
               and 'SocketMode=0660' in old.splitlines())
        if not known:
            raise RuntimeError('Unbekannter Wireshark-Socket.')
        if f'SocketGroup={gid}' in old.splitlines():
            replace(CAPTURE,old.replace('After=winbind.service\n',''),old)
        run('systemctl','daemon-reload')
        run('systemctl','enable','--now','cisco-lab-capture.socket')
        capture=Path('/run/cisco-lab-capture.sock').stat()
        if not stat.S_ISSOCK(capture.st_mode) or capture.st_gid!=gid or stat.S_IMODE(capture.st_mode)!=0o660:
            raise RuntimeError('Mitschnitt-Socket hat nicht die erwarteten Gruppenrechte.')


if __name__=='__main__':
    main()
