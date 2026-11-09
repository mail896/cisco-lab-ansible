"""Read-only comparison of the deployed LAB images and their configuration."""
from configparser import ConfigParser, Error
from hashlib import file_digest
from pathlib import Path
import stat
import subprocess
import sys


DESKTOP = Path('/usr/share/backgrounds/lab.png')
LOCK = Path('/usr/share/backgrounds/cisco-lab-login.jpg')
THEME = Path('/usr/share/sddm/themes/debian-breeze')
THEME_CONFIG = THEME / 'theme.conf.user'
LOCK_CONFIG = Path('/etc/cisco-lab/session-xdg/kscreenlockerrc')


def managed_file(path):
    info = path.lstat()
    return stat.S_ISREG(info.st_mode) and (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) == (0, 0, 0o644)


def matches_image(path, expected):
    if not managed_file(path):
        return False
    with path.open('rb') as image:
        return file_digest(image, 'sha256').hexdigest() == expected


def matches_config(path, entries):
    if not managed_file(path):
        return False
    config = ConfigParser(interpolation=None)
    config.optionxform = str
    with path.open() as file:
        config.read_file(file)
    if config.defaults():
        return False
    return all(config.get(section, key, fallback=None) == value for section, key, value in entries)


def greeter_loaded():
    result = subprocess.run(
        ['systemctl', 'show', 'sddm.service', '--timestamp=unix',
         '--property=ExecMainStartTimestamp', '--value'],
        capture_output=True, text=True, check=True, timeout=5,
    )
    started = int(result.stdout.strip().removeprefix('@'))
    # systemctl prints whole seconds. Round down to avoid a false match.
    return started * 1_000_000_000 >= max(path.stat().st_mtime_ns for path in (LOCK, THEME_CONFIG))


def main(desktop_sha256, lock_sha256):
    for path in (Path('/etc/cisco-lab/desktop.json'), LOCK_CONFIG):
        if not path.is_file() or path.is_symlink():
            raise SystemExit(f'Vorbereitete LAB-Datei fehlt: {path}')
    greeter_matches = False
    current = False
    try:
        greeter_matches = (
            Path('/usr/share/sddm/themes/debian-theme').resolve(strict=True) == THEME
            and matches_image(LOCK, lock_sha256)
            and matches_config(THEME_CONFIG, [('General', 'background', str(LOCK))])
            and subprocess.run(['runuser', '-u', 'sddm', '--', 'test', '-r', str(LOCK)],
                               check=False, timeout=5).returncode == 0
        )
        section = 'Greeter][Wallpaper][org.kde.image][General'
        current = greeter_matches and matches_image(DESKTOP, desktop_sha256) and matches_config(
            LOCK_CONFIG, [
                ('Greeter', 'WallpaperPlugin[$i]', 'org.kde.image'),
                (section, 'Image[$i]', LOCK.as_uri()),
                (section, 'PreviewImage[$i]', LOCK.as_uri()),
            ],
        )
    except (OSError, Error, UnicodeError, subprocess.SubprocessError):
        pass
    if current:
        print('LAB-Bildstand bereits vollständig installiert.')
        try:
            if greeter_loaded():
                print('SDDM hat den aktuellen Bildstand bereits geladen.')
        except (OSError, ValueError, subprocess.SubprocessError):
            pass


if __name__ == '__main__':
    main(*sys.argv[1:])
