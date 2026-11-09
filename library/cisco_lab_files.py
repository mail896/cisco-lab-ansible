#!/usr/bin/python3
"""Install fixed LAB files together, retaining per-file atomicity and permissions."""
import base64
import os
from pathlib import Path
import re
import stat
import tempfile

from ansible.module_utils.basic import AnsibleModule


def mode(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-7]{3,4}', value):
        raise ValueError('Dateimodus muss explizit oktal angegeben werden.')
    return int(value, 8)


def destination(value):
    path = Path(value)
    if not path.is_absolute() or '..' in path.parts or path == Path('/'):
        raise ValueError('Absoluter eindeutiger Zielpfad erforderlich.')
    return path


def install(directories, files, check=False):
    if os.geteuid() != 0:
        raise ValueError('LAB-Systemdateien benötigen root.')
    # Validate the entire payload before changing any path.
    dirs = [(destination(row['path']), mode(row.get('mode', '0755'))) for row in directories]
    entries = [(destination(row['dest']), mode(row.get('mode', '0644')),
                base64.b64decode(row['data'], validate=True)) for row in files]
    paths = [path for path, _ in dirs] + [path for path, _, _ in entries]
    if len(paths) != len(set(paths)):
        raise ValueError('Doppelter Zielpfad im LAB-Dateipaket.')
    for path in paths:
        if path.is_symlink():
            raise ValueError(f'Ziel ist ein Symlink: {path}')
        for parent in path.parents:
            if parent.is_symlink():
                raise ValueError(f'Elternpfad ist ein Symlink: {parent}')
            if parent.exists() and not parent.is_dir():
                raise ValueError(f'Elternpfad ist kein Verzeichnis: {parent}')
            if parent in {entry[0] for entry in entries}:
                raise ValueError(f'Elternpfad ist ein Dateiziel: {parent}')
    for path, _ in dirs:
        if path.exists() and not path.is_dir():
            raise ValueError(f'Verzeichnisziel ist kein Verzeichnis: {path}')
    for path, _, _ in entries:
        if path.exists() and not path.is_file():
            raise ValueError(f'Dateiziel ist keine reguläre Datei: {path}')
    changed = []

    def ensure_directory(path, wanted=0o755):
        if path.exists():
            return
        ensure_directory(path.parent)
        if not check:
            path.mkdir(mode=wanted)
            path.chmod(wanted)
        changed.append(str(path))

    for path, wanted in dirs:
        ensure_directory(path, wanted)
        info = path.stat() if path.exists() else None
        if info and (stat.S_IMODE(info.st_mode) != wanted or info.st_uid != 0 or info.st_gid != 0):
            changed.append(str(path))
            if not check:
                os.chown(path, 0, 0)
                path.chmod(wanted)
    for path, wanted, content in entries:
        ensure_directory(path.parent)
        info = path.stat() if path.exists() else None
        same = bool(info and path.read_bytes() == content)
        if same and stat.S_IMODE(info.st_mode) == wanted and info.st_uid == 0 and info.st_gid == 0:
            continue
        changed.append(str(path))
        if check:
            continue
        if same:
            os.chown(path, 0, 0)
            path.chmod(wanted)
            continue
        fd, name = tempfile.mkstemp(prefix='.lab-file-', dir=path.parent)
        temporary = Path(name)
        try:
            with os.fdopen(fd, 'wb') as output:
                output.write(content)
                output.flush()
                os.fsync(output.fileno())
                os.fchown(output.fileno(), 0, 0)
                os.fchmod(output.fileno(), wanted)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
    return dict(changed=bool(changed), paths=sorted(set(changed)))


def main():
    module = AnsibleModule(argument_spec=dict(
        directories=dict(type='list', elements='dict', default=[]),
        files=dict(type='list', elements='dict', default=[], options=dict(
            dest=dict(type='path', required=True), mode=dict(type='str', default='0644'),
            data=dict(type='str', required=True, no_log=True)))), supports_check_mode=True)
    try:
        module.exit_json(**install(module.params['directories'], module.params['files'], module.check_mode))
    except (OSError, ValueError, KeyError, TypeError) as error:
        module.fail_json(msg=str(error))


if __name__ == '__main__':
    main()
