"""Copy the Teacher's chosen lock-screen image to the active SDDM theme."""
import configparser
import os
from pathlib import Path
import pwd
import shutil
import socket
import stat
import tempfile
from urllib.parse import unquote, urlsplit


def config(paths):
    result = configparser.ConfigParser(interpolation=None)
    result.optionxform = str
    result.read([str(path) for path in paths])
    return result


def main():
    if os.geteuid() != 0 or socket.gethostname().split('.')[0] != 'LAB-Teacher':
        raise SystemExit('Nur mit sudo auf LAB-Teacher ausführen.')
    home = Path(pwd.getpwnam('marc').pw_dir)
    lock = config([home / '.config/kscreenlockerrc'])
    section = 'Greeter][Wallpaper][org.kde.image][General'
    values = dict(lock.items(section))
    image = next((value for key, value in values.items() if key.split('[')[0] == 'Image'), '')
    url = urlsplit(image)
    if url.scheme not in ('', 'file') or url.netloc not in ('', 'localhost'):
        raise ValueError('Sperrbild muss eine lokale Bilddatei sein.')
    image = unquote(url.path).replace('$HOME', str(home)).replace('${HOME}', str(home))
    source = Path(image).expanduser()
    if not source.is_file() or source.suffix.lower() not in ('.jpg', '.jpeg', '.png', '.webp', '.svg'):
        raise ValueError('Keine unterstützte Sperrbilddatei gefunden: ' + str(source))
    if source.stat().st_size > 48 * 1024 * 1024:
        raise ValueError('Sperrbild größer als 48 MiB.')
    paths = [*sorted(Path('/usr/lib/sddm/sddm.conf.d').glob('*.conf')),
             *sorted(Path('/etc/sddm.conf.d').glob('*.conf')), Path('/etc/sddm.conf')]
    settings = config(paths)
    theme_dir = settings.get('Theme', 'ThemeDir', fallback='/usr/share/sddm/themes')
    name = settings.get('Theme', 'Current', fallback='debian-theme')
    theme = (Path(theme_dir) / name).resolve(strict=True)
    if theme not in [Path('/usr/share/sddm/themes') / item for item in ('breeze', 'debian-breeze')]:
        raise ValueError('Unerwartetes Theme: ' + str(theme))
    override = theme / 'theme.conf.user'
    folder = Path('/usr/local/share/cisco-lab-teacher-login')
    folder.mkdir(mode=0o755, exist_ok=True)
    destination = folder / ('background' + source.suffix.lower())
    for path in (override, destination, folder, *folder.parents, theme):
        if path.exists() or path.is_symlink():
            info = path.lstat()
            if info.st_uid != 0 or info.st_mode & 0o022 or stat.S_ISLNK(info.st_mode):
                raise ValueError('Nicht rootgeschütztes Ziel: ' + str(path))
    content = config([override])
    if not content.has_section('General'):
        content.add_section('General')
    content.set('General', 'background', str(destination))
    if override.exists():
        backup = override.with_name('theme.conf.user.before-teacher-background')
        if not backup.exists():
            with backup.open('xb') as f:
                os.fchmod(f.fileno(), 0o600)
                f.write(override.read_bytes())
    shutil.copyfile(source, destination)
    destination.chmod(0o644)
    with tempfile.NamedTemporaryFile(mode='w', dir=theme, delete=False) as f:
        temporary = Path(f.name)
        try:
            os.fchmod(f.fileno(), 0o644)
            content.write(f, space_around_delimiters=False)
            f.flush()
            os.fsync(f.fileno())
            temporary.replace(override)
        finally:
            temporary.unlink(missing_ok=True)
    print('Sperrbild für SDDM übernommen:', source)
    print('Theme:', name, '– sichtbar beim nächsten Start des Anmeldebildschirms.')


if __name__ == '__main__':
    main()
