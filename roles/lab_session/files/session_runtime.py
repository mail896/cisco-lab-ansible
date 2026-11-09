#!/usr/bin/python3
"""LAB-only X11 session initialization; no periodic enforcement or home writes."""
import grp
import configparser
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

AUTOSTART_NAMES = ('lab-session-runtime.desktop', 'lab-kde-session-cleanup.desktop',
                   'lab-kde-effects.desktop', 'monitor75.desktop')


def prepare_autostarts(config_home, templates):
    """Prepare known entries before the systemd generator, as the LAB user."""
    directory = config_home / 'autostart'
    for path in (config_home, directory):
        if path.is_symlink():
            raise ValueError('Umgeleitetes Konfigurationsverzeichnis; keine Aenderung')
    pending = []
    for name in AUTOSTART_NAMES:
        target = directory / name
        if target.is_symlink():
            raise ValueError('Autostart-Symlink; keine Aenderung')
        if target.exists():
            old = configparser.ConfigParser(interpolation=None)
            old.read_string(target.read_text())
            managed = old.getboolean('Desktop Entry', 'X-Cisco-Lab-Managed', fallback=False)
            hidden = old.getboolean('Desktop Entry', 'Hidden', fallback=False)
            if not managed and not (name != 'lab-session-runtime.desktop' and hidden):
                raise ValueError('Unbekannter persoenlicher Autostart: ' + name)
        policy = configparser.ConfigParser(interpolation=None)
        policy.optionxform = str
        policy.read_string((templates / name).read_text())
        policy.set('Desktop Entry', 'X-Cisco-Lab-Managed', 'true')
        output = io.StringIO()
        policy.write(output, space_around_delimiters=False)
        pending.append((target, output.getvalue()))
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    for target, text in pending:
        if target.is_file() and target.read_text() == text:
            continue
        fd, name = tempfile.mkstemp(prefix='.lab-session-', dir=directory)
        try:
            with os.fdopen(fd, 'w') as stream:
                stream.write(text)
            os.replace(name, target)
        finally:
            if os.path.exists(name):
                os.unlink(name)


def eligible(group):
    if os.geteuid() == 0:
        return False
    gid = grp.getgrnam(group).gr_gid
    return gid in set(os.getgroups()) | {os.getgid()}


def monitor_commands(output):
    """Use an advertised ~75Hz rate at the current resolution, never enable outputs."""
    commands = []
    connected = None
    for line in output.splitlines():
        if line and not line[0].isspace():
            match = re.match(r'^(\S+) connected(?:\s|$)', line)
            connected = match.group(1) if match else None
            continue
        fields = line.split()
        if not connected or len(fields) < 2 or not re.fullmatch(r'\d+x\d+\S*', fields[0]):
            continue
        rates = fields[1:]
        current = next((r for r in rates if '*' in r), None)
        if current is None:
            continue
        candidates = []
        for rate in rates:
            number = rate.rstrip('*+')
            try:
                if 74 <= float(number) <= 76:
                    candidates.append(number)
            except ValueError:
                continue
        if candidates:
            chosen = min(candidates, key=lambda value: abs(float(value) - 75))
            if current.rstrip('*+') != chosen:
                commands.append(['/usr/bin/xrandr','--output',connected,'--mode',fields[0],'--rate',chosen])
    return commands


def initialize(run=subprocess.run, env=None):
    env = os.environ if env is None else env
    if env.get('XDG_SESSION_TYPE') != 'x11' or not env.get('DISPLAY'):
        raise ValueError('LAB-Sitzungsvorgaben benoetigen eine X11-Sitzung mit DISPLAY.')
    errors = []
    def execute(args):
        try:
            result = run(args, capture_output=True, text=True, timeout=15)
            if result.returncode:
                errors.append(f'{args[0]}: {result.stderr.strip()}')
            return result
        except (OSError, subprocess.TimeoutExpired) as error:
            errors.append(str(error))
            return None
    for args in (['/usr/bin/xset','-dpms'], ['/usr/bin/xset','s','off'],
                 ['/usr/bin/xset','s','noblank'], ['/usr/bin/numlockx','on']):
        execute(args)
    query = execute(['/usr/bin/xrandr','--query'])
    if query is not None and query.returncode == 0:
        for command in monitor_commands(query.stdout):
            execute(command)
    if errors:
        raise ValueError('\n'.join(errors))


def main():
    settings = json.loads(Path('/etc/cisco-lab/session.json').read_text())
    if not eligible(settings['group']):
        return 3
    if sys.argv[1:] == ['--eligible']:
        return 0
    if sys.argv[1:] == ['--prepare-autostart']:
        config_home = Path(os.environ.get('XDG_CONFIG_HOME', str(Path.home() / '.config')))
        if config_home != Path.home() / '.config' or Path.home().is_symlink():
            raise ValueError('Unbekannter Konfigurationspfad; keine Aenderung')
        prepare_autostarts(config_home, Path('/etc/cisco-lab/session-xdg/autostart'))
        return 0
    if sys.argv[1:]:
        raise ValueError('Unbekanntes Argument')
    initialize()
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError) as error:
        print(f'LAB session: {error}', file=sys.stderr)
        sys.exit(1)
