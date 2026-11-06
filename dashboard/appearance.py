"""Select approved local image files and stage normalized deployment assets."""
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
DEFAULTS = {'desktop': ROOT/'files/lab.png', 'lock': ROOT/'files/network.jpg'}
FOLDER = Path.home()/'Bilder/CISCO-LAB'
MAX_FILE = 48 * 1024 * 1024


class Appearance:
    def __init__(self, directory):
        self.directory = directory
        directory.mkdir(mode=0o700,parents=True,exist_ok=True)
        FOLDER.mkdir(mode=0o700,parents=True,exist_ok=True)
        self.settings = directory/'selection.json'

    def candidates(self):
        return {file.name:file for file in sorted(FOLDER.iterdir())
                if file.is_file() and not file.is_symlink()
                and file.suffix.lower() in ('.jpg','.jpeg','.png')}

    def active(self):
        if not self.settings.exists():
            return {kind: ('',path) for kind,path in DEFAULTS.items()}
        state = json.loads(self.settings.read_text())
        return {kind:(state[kind]['label'], self.directory/state[kind]['file'])
                if state[kind]['file'] else ('',path) for kind,path in DEFAULTS.items()}

    def choices(self):
        files = self.candidates()
        images = [name for name, path in files.items() if 0 < path.stat().st_size <= MAX_FILE]
        return {'folder': str(FOLDER), 'images': images, 'excluded': len(files) - len(images),
                'limit_mib': MAX_FILE // (1024 * 1024),
                'selected': {kind: entry[0] for kind, entry in self.active().items()}}

    def select(self, desktop, lock):
        files=self.candidates()
        selections = {}
        for kind,name in (('desktop',desktop),('lock',lock)):
            if not name:
                selections[kind]={'label':'','file':''}
                continue
            if name not in files:
                raise ValueError('Bitte ein PNG- oder JPEG-Bild aus dem LAB-Bilderordner wählen.')
            with files[name].open('rb') as file:
                source = file.read(MAX_FILE + 1)
            if not 0 < len(source) <= MAX_FILE:
                raise ValueError('Bilddatei ist leer oder größer als 48 MiB.')
            if source.startswith(b'\x89PNG\r\n\x1a\n'):
                decoder='png'
            elif source.startswith(b'\xff\xd8\xff'):
                decoder='jpeg'
            else:
                raise ValueError('Bildinhalt ist kein unterstütztes PNG oder JPEG.')
            output_format='png' if kind=='desktop' else 'jpeg'
            with tempfile.TemporaryDirectory(dir=self.directory) as tmp:
                data=Path(tmp)/'input'
                data.write_bytes(source)
                image=Path(tmp)/'output'
                command=['/usr/bin/magick','-limit','memory','128MiB','-limit','map','128MiB',
                         '-limit','disk','768MiB','-limit','width','10000','-limit','height','10000',
                         '-limit','thread','2','-define','jpeg:size=3840x2160',f'{decoder}:{data}[0]','-auto-orient','-strip',
                         '-resize','3840x2160>','-background','#17263a','-alpha','remove',
                         f'{output_format}:{image}']
                result=subprocess.run(command,capture_output=True,timeout=30)
                if result.returncode:
                    raise ValueError('Bild konnte nicht sicher verarbeitet werden.')
                normalized=image.read_bytes()
                digest=hashlib.sha256(normalized).hexdigest()
                filename=f'{digest}.{output_format}'
                image.replace(self.directory/filename)
            selections[kind]={'label':name,'file':filename}
        temp=self.settings.with_suffix('.tmp')
        temp.write_text(json.dumps(selections,ensure_ascii=False))
        temp.replace(self.settings)
        return self.choices()
