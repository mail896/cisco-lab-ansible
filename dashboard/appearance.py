"""Select approved local image files and stage normalized deployment assets."""
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import threading
from collections import OrderedDict

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
        self.preview_cache = OrderedDict()
        self.preview_lock = threading.Lock()
        self.preview_slots = threading.BoundedSemaphore(2)

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
        active = self.active()
        return {'folder': str(FOLDER), 'images': images, 'excluded': len(files) - len(images),
                'limit_mib': MAX_FILE // (1024 * 1024),
                'selected': {kind: entry[0] for kind, entry in active.items()},
                'previews': {kind: {'active': self.available_preview_token(entry[1]),
                                    'standard': self.available_preview_token(DEFAULTS[kind])}
                             for kind, entry in active.items()},
                'image_previews': {name: self.available_preview_token(files[name]) for name in images}}

    def available_preview_token(self, path):
        try:
            return self.preview_token(path)
        except (OSError, ValueError):
            return None

    @staticmethod
    def preview_token(path, info=None):
        info = path.lstat() if info is None else info
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= MAX_FILE:
            raise ValueError('Keine unterstützte Bilddatei.')
        signature = (str(path), info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
        return hashlib.sha256(repr(signature).encode()).hexdigest()

    def upload(self, name, source):
        if (not isinstance(name, str) or not name or name.startswith('.') or len(name.encode()) > 180
                or re.search(r'[/\\\x00-\x1f\x7f]', name)
                or Path(name).suffix.lower() not in ('.png', '.jpg', '.jpeg')):
            raise ValueError('Bitte eine PNG- oder JPEG-Datei mit einem gültigen Dateinamen wählen.')
        normalized = self.normalize(source, 'png' if Path(name).suffix.lower() == '.png' else 'jpeg')
        # Exclusive creation in the opened library directory never replaces an
        # existing image (or link), including when two uploads share a name.
        directory = os.open(FOLDER, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            stem, suffix = Path(name).stem, Path(name).suffix
            for number in range(1, 1001):
                candidate = name if number == 1 else f'{stem}-{number}{suffix}'
                try:
                    descriptor = os.open(candidate, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
                except FileExistsError:
                    continue
                try:
                    with os.fdopen(descriptor, 'wb') as output:
                        output.write(normalized); output.flush(); os.fsync(output.fileno())
                except BaseException:
                    os.unlink(candidate, dir_fd=directory)
                    raise
                os.fsync(directory)
                return {'uploaded': candidate, 'choices': self.choices()}
            raise ValueError('Zu viele Bilder mit diesem Namen. Bitte die Datei umbenennen.')
        finally:
            os.close(directory)

    def normalize(self, data, output_format):
        if not 0 < len(data) <= MAX_FILE:
            raise ValueError('Bilddatei ist leer oder größer als 48 MiB.')
        decoder = 'png' if data.startswith(b'\x89PNG\r\n\x1a\n') else 'jpeg' if data.startswith(b'\xff\xd8\xff') else None
        if not decoder:
            raise ValueError('Bildinhalt ist kein unterstütztes PNG oder JPEG.')
        with tempfile.TemporaryDirectory(dir=self.directory, prefix='.image-') as tmp:
            input_file, output_file = Path(tmp)/'input', Path(tmp)/'output'
            input_file.write_bytes(data)
            result = subprocess.run([sys.executable, '-I', '-B', str(Path(__file__).with_name('normalize_image.py')),
                                     str(input_file), str(output_file), decoder, output_format], capture_output=True, timeout=30)
            if result.returncode:
                raise ValueError('Bild konnte nicht sicher verarbeitet werden. Bitte ein gültiges PNG oder JPEG bis 24 Megapixel wählen.')
            limit = 1024 * 1024 if output_format == 'preview' else MAX_FILE
            with output_file.open('rb') as file:
                result = file.read(limit + 1)
            if not 0 < len(result) <= limit:
                raise ValueError('Verarbeitetes Bild ist zu groß.')
            return result

    def preview(self, token):
        # Resolve only an opaque revision of a known image. No user paths or raw
        # files reach the browser; decoding remains in the bounded subprocess.
        if not re.fullmatch('[a-f0-9]{64}', token):
            raise FileNotFoundError
        paths = set(self.candidates().values()) | set(DEFAULTS.values()) | {entry[1] for entry in self.active().values()}
        source = None
        for path in paths:
            try:
                if self.preview_token(path) == token:
                    source = path
                    break
            except (OSError, ValueError):
                continue
        if source is None:
            raise FileNotFoundError
        with self.preview_lock:
            if token in self.preview_cache:
                self.preview_cache.move_to_end(token)
                return self.preview_cache[token]
        if not self.preview_slots.acquire(blocking=False):
            raise ValueError('Bildvorschau wird bereits verarbeitet. Bitte erneut laden.')
        try:
            with os.fdopen(os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), 'rb') as file:
                if self.preview_token(source, os.fstat(file.fileno())) != token:
                    raise FileNotFoundError
                data = file.read(MAX_FILE + 1)
            preview = self.normalize(data, 'preview')
            with self.preview_lock:
                self.preview_cache[token] = preview
                self.preview_cache.move_to_end(token)
                while len(self.preview_cache) > 24:
                    self.preview_cache.popitem(last=False)
            return preview
        finally:
            self.preview_slots.release()

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
                command=[sys.executable, '-I', '-B', str(Path(__file__).with_name('normalize_image.py')),
                         str(data), str(image), decoder, output_format]
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
