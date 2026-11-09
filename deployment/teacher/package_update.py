"""One local, narrow Teacher code update. Generated payload contains no key material."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import sys
import tempfile

PATCHES = None


def contents(path,row):
    before=path.read_bytes() if path.exists() else None
    digest=hashlib.sha256(before).hexdigest() if before is not None else None
    if digest==row['new']:return before,before
    if digest!=row['old']:
        raise ValueError('Unerwarteter Dateistand; nichts geändert: '+str(path))
    lines=before.decode().splitlines(keepends=True) if before is not None else []
    for first,last,text in reversed(row['edits']):lines[first:last]=text.splitlines(keepends=True)
    after=''.join(lines).encode()
    if hashlib.sha256(after).hexdigest()!=row['new']:raise ValueError('Update-Prüfsumme stimmt nicht.')
    compile(after,str(path),'exec')
    return before,after


def atomic(path,body,mode):
    with tempfile.NamedTemporaryFile(prefix='.lab-package-update-',dir=path.parent,delete=False) as out:
        temporary=Path(out.name)
        try:
            out.write(body);out.flush();os.fsync(out.fileno());os.fchmod(out.fileno(),mode)
            temporary.replace(path)
        finally:temporary.unlink(missing_ok=True)


def main():
    sys.path.insert(0,'/usr/local/lib/cisco-lab-teacher')
    import receiver
    receiver.require_teacher()
    if PATCHES is None:raise ValueError('Erzeugter Update-Quelltext fehlt.')
    with receiver.lock('endpoint'),receiver.lock('worker'):
        if receiver.active() not in ('inactive','failed'):raise RuntimeError('Teacher-Wartung läuft noch.')
        changes=[]
        for row in PATCHES:
            path=Path(row['path'])
            if path.exists() or path.is_symlink():
                info=path.lstat()
                if not stat.S_ISREG(info.st_mode) or info.st_uid!=0 or info.st_mode&0o022:
                    raise ValueError('Teacher-Datei nicht rootgeschützt: '+str(path))
            before,after=contents(path,row)
            if before!=after:changes.append((path,before,after,row['mode']))
        if not changes:
            print('Teacher-Paketzugang bereits aktuell. Keine Änderung.');return
        marker=receiver.STATE/'install.json';marker_bytes=marker.read_bytes()
        data=json.loads(marker_bytes)
        backup=Path(tempfile.mkdtemp(prefix='cisco-lab-package-update-',dir='/var/backups'))
        shutil.copy2(marker,backup/marker.name)
        for path,before,_,_ in changes:
            if before is not None:shutil.copy2(path,backup/path.name)
        try:
            for path,_,after,mode in changes:
                atomic(path,after,mode);data['files'][str(path)]=hashlib.sha256(after).hexdigest()
            atomic(marker,(json.dumps(data,indent=2)+'\n').encode(),0o600)
        except BaseException:
            for path,before,_,mode in changes:
                if before is None:path.unlink(missing_ok=True)
                else:atomic(path,before,mode)
            atomic(marker,marker_bytes,0o600)
            raise
        print('Teacher-Paketzugang aktualisiert. Keine Paketinstallation, kein Neustart, kein Timer.')
        print('Kleine Konfigurations-Rückfallkopie: '+str(backup))


if __name__=='__main__':main()
