"""Private, content-addressed package library for administrator-provided .deb files."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile

from dashboard.admission import read_settings, write_private
from dashboard.deb_client import FIELDS, MAX_DEB, metadata


def default_directory():
    return Path.home()/'.local/state/cisco-lab-dashboard/software/packages'


class OwnPackages:
    def __init__(self,directory=None):
        self.directory=directory or default_directory()

    def rows(self):
        return sorted((read_settings(path) for path in self.directory.glob('*.json')),key=lambda row:row['name'])

    def snapshot(self,names):
        return [{key:row[key] for key in FIELDS} for row in self.rows() if row['name'] in names]

    def path(self,row):
        from dashboard.deb_client import descriptors
        descriptors([row])
        path=self.directory/(row['sha256']+'.deb')
        if path.is_symlink() or not path.is_file() or path.stat().st_size!=row['size']:
            raise ValueError('Eigene Paketdatei fehlt oder wurde verändert: '+row['name'])
        return path

    def upload(self,stream,size,filename):
        if (type(size) is not int or not 1<=size<=MAX_DEB or not isinstance(filename,str)
                or len(filename)>255 or Path(filename).name!=filename or not filename.lower().endswith('.deb')):
            raise ValueError('Bitte eine .deb-Datei bis 512 MiB auswählen.')
        self.directory.mkdir(mode=0o700,parents=True,exist_ok=True)
        used=sum(path.stat().st_size for path in self.directory.glob('*.deb'))
        if used+size>4*1024**3 or shutil.disk_usage(self.directory).free<size+2*1024**3:
            raise ValueError('Nicht genug freier Platz im LAB-Paketbereich (höchstens 4 GiB).')
        with tempfile.NamedTemporaryFile(prefix='.upload-',dir=self.directory,delete=False) as out:
            temporary=Path(out.name);digest=hashlib.sha256();remaining=size
            try:
                while remaining:
                    block=stream.read(min(1048576,remaining))
                    if not block:raise ValueError('Paketdatei nicht vollständig übertragen.')
                    out.write(block);digest.update(block);remaining-=len(block)
                out.flush();os.fsync(out.fileno())
                row=dict(metadata(temporary),sha256=digest.hexdigest(),size=size)
                if len(self.rows())>=100 and not any(item['name']==row['name'] for item in self.rows()):
                    raise ValueError('Höchstens 100 eigene Paketnamen verwalten.')
                for existing in self.rows():
                    if existing['name']==row['name'] and existing['version']==row['version'] and existing['sha256']!=row['sha256']:
                        raise ValueError('Diese Version hat bereits einen anderen Inhalt. Bitte die Paketversion erhöhen.')
                destination=self.directory/(row['sha256']+'.deb')
                temporary.replace(destination);destination.chmod(0o600)
                marker=self.directory/(row['name']+'.json');scratch=marker.with_suffix('.tmp')
                write_private(scratch,json.dumps(row,ensure_ascii=False));scratch.replace(marker)
                return row
            finally:temporary.unlink(missing_ok=True)
