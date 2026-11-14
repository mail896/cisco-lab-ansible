"""Read completed LAB APT transactions in one confirmed maintenance window."""
from datetime import datetime
import gzip
import json
from pathlib import Path
import re
import shlex
import socket
import sys

MAX_BYTES = 4 * 1024 * 1024
PACKAGE = r'[a-z0-9][a-z0-9+.-]*(?::[a-z0-9-]+)?'


def unavailable(reason):
    return dict(status='unavailable', reason=reason)


def transactions(text):
    for block in re.split(r'\n\s*\n', text.strip()):
        fields = {}
        for line in block.splitlines():
            key, separator, value = line.partition(':')
            if separator:
                if key in fields:
                    raise ValueError('Doppeltes Feld im APT-Verlauf.')
                fields[key] = value.strip()
        if 'Start-Date' in fields:
            yield fields
        elif block.strip():
            raise ValueError('Unvollständiger APT-Verlauf.')


def summarize(texts, start, end, timezone=None):
    """Missing/ambiguous history is unknown, never evidence of zero updates."""
    def stamp(value):
        parsed = datetime.strptime(value, '%Y-%m-%d  %H:%M:%S')
        return parsed.replace(tzinfo=timezone) if timezone else parsed.astimezone()
    result = dict(status='recorded', source='apt-history', upgraded=[], installed=[], removed=[])
    earliest = None
    for text in texts:
        for row in transactions(text):
            began = stamp(row['Start-Date'])
            earliest = min(earliest, began) if earliest else began
            if began > end:
                continue
            finished = stamp(row['End-Date']) if row.get('End-Date') else None
            if began < start:
                if finished is None or finished >= start:
                    return unavailable('Paketvorgang überschneidet den Wartungsbeginn.')
                continue
            if finished is None or finished > end:
                return unavailable('Paketvorgang im Wartungsfenster nicht eindeutig abgeschlossen.')
            command = shlex.split(row.get('Commandline', ''))
            lab = (command and Path(command[0]).name == 'apt-get'
                   and 'DPkg::Lock::Timeout=30' in command and '-y' in command)
            upgrade = lab and all(arg in command for arg in ('--no-remove', '--with-new-pkgs', 'upgrade'))
            cleanup = lab and all(arg in command for arg in ('--no-auto-remove', 'remove'))
            if not (upgrade or cleanup) or any(arg in command for arg in ('-s', '--simulate', '--download-only', '--just-print')):
                return unavailable('Paketvorgang lässt sich nicht eindeutig der LAB-Wartung zuordnen.')
            for field, key in (('Upgrade', 'upgraded'), ('Install', 'installed'), ('Remove', 'removed'), ('Purge', 'removed')):
                value = row.get(field, '')
                if not value:
                    continue
                entries = re.findall('('+PACKAGE+r') \(([^()]*)\)(?:, |$)', value)
                if ', '.join(name+' ('+versions+')' for name, versions in entries) != value:
                    return unavailable('Paketliste im APT-Verlauf nicht vollständig lesbar.')
                for name, versions in entries:
                    result[key].append(dict(name=name, versions=versions))
    if earliest is None or earliest > start:
        return unavailable('APT-Verlauf deckt den Wartungsbeginn nicht nachweisbar ab.')
    return result


def read_history(start, end, directory=Path('/var/log/apt')):
    files = sorted(path for path in directory.glob('history.log*')
                   if re.fullmatch(r'history\.log(?:\.[1-9][0-9]?(?:\.gz)?)?', path.name))
    if not files or len(files) > 12:
        return unavailable('APT-Verlauf fehlt oder überschreitet den begrenzten Leseumfang.')
    contents = []
    remaining = MAX_BYTES
    for path in files:
        if path.is_symlink() or not path.is_file():
            return unavailable('APT-Verlauf ist keine reguläre Datei.')
        opener = gzip.open if path.suffix == '.gz' else open
        with opener(path, 'rb') as source:
            body = source.read(remaining + 1)
        remaining -= len(body)
        if remaining < 0:
            return unavailable('APT-Verlauf überschreitet den begrenzten Leseumfang.')
        contents.append(body.decode('utf-8'))
    return summarize(contents, start, end)


if __name__ == '__main__':
    # Sent over the pinned SSH connection, without installing a client payload.
    if len(sys.argv) != 4 or socket.gethostname() != sys.argv[1]:
        raise SystemExit('Falsche Geräteidentität.')
    try:
        start, end = map(datetime.fromisoformat, sys.argv[2:])
        if start.utcoffset() is None or end.utcoffset() is None or not start <= end:
            raise ValueError('Ungültiges Wartungsfenster.')
        report = read_history(start, end)
    except (OSError, ValueError, EOFError):
        report = unavailable('APT-Verlauf konnte nicht eindeutig gelesen werden.')
    print(json.dumps(report, ensure_ascii=False))
