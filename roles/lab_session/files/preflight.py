#!/usr/bin/python3
"""Read-only deployment prerequisites. Do not execute during local preparation."""
import configparser
from pathlib import Path
import subprocess


def conflicting_sddm(actual_text, managed_text):
    def read(text):
        config = configparser.ConfigParser(interpolation=None)
        config.optionxform = str
        config.read_string(text)
        return config
    actual, managed = read(actual_text), read(managed_text)
    return [f'{section}/{key}' for section in managed.sections()
            for key, value in managed[section].items()
            if actual.has_option(section,key) and actual.get(section,key) != value]


def main():
    sessions = subprocess.check_output(['loginctl','list-sessions','--no-legend'],text=True)
    for line in sessions.splitlines():
        info = subprocess.check_output(['loginctl','show-session',line.split()[0],'-p','Class','-p','Type','-p','Remote'],text=True)
        fields = dict(row.split('=',1) for row in info.splitlines())
        interactive = fields.get('Type') in ('x11','wayland') or (fields.get('Type')=='tty' and fields.get('Remote')=='no')
        if interactive and fields.get('Class','').startswith('user'):
            raise ValueError('Lokale Benutzersitzung vorhanden; zuerst abmelden.')
    if not any(Path('/usr/share/xsessions',name).is_file() for name in ('plasmax11.desktop','plasma.desktop')):
        raise ValueError('Keine bekannte Plasma-X11-Sitzung gefunden.')
    current = Path('/etc/sddm.conf')
    if current.exists():
        conflicts = conflicting_sddm(current.read_text(), MANAGED_SDDM)
        if conflicts:
            raise ValueError('/etc/sddm.conf hat Vorrang und widerspricht: ' + ', '.join(conflicts))
    for command in ('is-active','is-enabled'):
        result = subprocess.run(['systemctl',command,'sddm-clear-lastuser.service'],capture_output=True,text=True)
        if result.stdout.strip() in ('active','enabled','enabled-runtime'):
            raise ValueError('Alter sddm-clear-lastuser.service ist aktiv/aktiviert; vor Migration gezielt klaeren.')


# Replaced by the role with the exact config being deployed.
MANAGED_SDDM = ''
if __name__ == '__main__':
    main()
