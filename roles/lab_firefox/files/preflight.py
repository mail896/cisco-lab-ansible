"""Read-only migration guard; preserve unrelated existing enterprise settings."""
import json
from pathlib import Path
import stat
import sys
import zipfile


# Previous LAB policy values rejected by Firefox ESR's Preferences policy.
# The dedicated account and first-run policies already cover the intended settings.
RETIRED_PREFERENCES = {
    'startup.homepage_welcome_url': {'Value': '', 'Status': 'locked'},
    'startup.homepage_welcome_url.additional': {'Value': '', 'Status': 'locked'},
    'identity.fxaccounts.enabled': {'Value': False, 'Status': 'locked'},
}


def unknown_settings(current, planned, path=''):
    unknown = []
    if isinstance(current, dict) and isinstance(planned, dict):
        for key, value in current.items():
            location = path + '/' + key
            if key not in planned:
                if (path == '/policies/Preferences' and key in RETIRED_PREFERENCES
                        and value == RETIRED_PREFERENCES[key]):
                    continue
                unknown.append(location)
            else:
                unknown.extend(unknown_settings(value, planned[key], location))
    return unknown


def policy_path():
    packaged = Path('/usr/lib/firefox-esr/distribution')
    shared = Path('/usr/share/firefox-esr/distribution')
    directory = packaged
    if packaged.is_symlink():
        if (packaged.lstat().st_uid != 0
                or packaged.readlink() != Path('../../share/firefox-esr/distribution')
                or packaged.resolve(strict=True) != shared):
            raise ValueError('Unbekannte Firefox-Paketumleitung; vor Migration klaeren.')
        directory = shared
    for parent in {directory, *directory.parents, *packaged.parents}:
        if parent == packaged and not parent.exists():
            continue  # The role can create a missing ordinary distribution directory.
        info = parent.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise ValueError(f'Unsicheres Firefox-Policy-Verzeichnis: {parent}')
    for override in (Path('/etc/firefox/policies/policies.json'),
                     Path('/etc/firefox-esr/policies/policies.json')):
        if override.exists() or override.is_symlink():
            raise ValueError(f'Vorrangige Firefox-Policy zuerst pruefen: {override}')
    path = directory / 'policies.json'
    if path.is_symlink():
        raise ValueError('Umgeleitete Firefox-Policy; vor Migration klaeren.')
    return path


def main():
    planned = json.load(sys.stdin)
    path = policy_path()
    if path.exists():
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise ValueError('Unerwartete Policy-Eigentuemer/Schreibrechte.')
        current = json.loads(path.read_text())
        if not isinstance(current, dict) or not isinstance(current.get('policies'), dict):
            raise ValueError('Unerwartete vorhandene Policy-Struktur.')
        unknown = unknown_settings(current, planned)
        if unknown:
            raise ValueError('Zusaetzliche vorhandene Policies wuerden entfallen: '
                             + ', '.join(unknown))
    # Check names against the schema shipped with this actual Firefox binary.
    # This is a name-compatibility check, not a full JSON Schema validator.
    with zipfile.ZipFile('/usr/lib/firefox-esr/browser/omni.ja') as archive:
        schema = json.loads(archive.read('modules/policies/policies-schema.json'))
    unknown = set(planned['policies']) - set(schema['properties'])
    if unknown:
        raise ValueError('Firefox kennt diese Policies nicht: ' + ', '.join(sorted(unknown)))
    print(path)


if __name__ == '__main__':
    main()
