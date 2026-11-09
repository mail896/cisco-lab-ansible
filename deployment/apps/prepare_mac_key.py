"""Create a public authorized_keys line restricted to the LAB Mac receiver."""
import base64
import binascii
from pathlib import Path

FOLDER = Path('/var/lib/cisco-lab/.ssh')
SOURCE = FOLDER / 'id_ed25519_backup.pub'
TARGET = FOLDER / 'mac-backup-authorized.pub'


def main():
    public = SOURCE.read_text().strip()
    fields = public.split()
    if len(fields) != 3 or fields[0] != 'ssh-ed25519' or any('\n' in field for field in fields):
        raise ValueError('Unerwartetes Format des öffentlichen LAB-Backup-Schlüssels.')
    try:
        if len(base64.b64decode(fields[1], validate=True)) < 48:
            raise ValueError('Öffentlicher LAB-Backup-Schlüssel ist zu kurz.')
    except binascii.Error as error:
        raise ValueError('Öffentlicher LAB-Backup-Schlüssel ist ungültig.') from error
    line = ('from="203.0.113.200",restrict,'
            'command="/Volumes/Repo/cisco-lab/.receiver.sh" ' + public + '\n')
    if TARGET.exists():
        if TARGET.read_text() != line:
            raise ValueError('Vorhandene Mac-Schlüsselzeile weicht ab.')
    else:
        with TARGET.open('x') as output:
            output.write(line)
        TARGET.chmod(0o600)
    print('Eingeschränkte öffentliche Mac-Schlüsselzeile vorbereitet.')


if __name__ == '__main__':
    main()
