#!/usr/bin/python3
"""Prepare Plasma configuration BEFORE plasmashell starts, as the LAB user.

No sleeping, D-Bus, root writes to homes, or dependence on generated applet IDs.
Existing layouts are discovered by plugin type; fresh homes get a complete seed.
"""
import configparser
import grp
import io
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import time

AUTOSTART_NAMES = ('lab-panel-favorites.desktop', 'lab-wallpaper.desktop',
                   'lab-kde-session-cleanup.desktop', 'lab-clock.desktop')


def parser(text):
    result = configparser.RawConfigParser(interpolation=None, strict=True,
                                          delimiters=('=',), comment_prefixes=('#',),
                                          empty_lines_in_values=False)
    result.optionxform = str
    if text.strip():
        result.read_string(text)
    return result


def render(text, settings):
    config = parser(text)
    def put(section, **values):
        if not config.has_section(section):
            config.add_section(section)
        for key, value in values.items():
            config.set(section, key, str(value))
    ids = [int(n) for s in config.sections() for n in re.findall(r'\[(\d+)\]', '[' + s + ']')]
    next_id = max(ids, default=0) + 1
    def allocate():
        nonlocal next_id
        value = next_id
        next_id += 1
        return value
    desktop_groups = [s for s in config.sections()
                      if re.fullmatch(r'Containments\]\[\d+', s)
                      and config.get(s, 'plugin', fallback='') in
                      ('org.kde.plasma.folder', 'org.kde.desktopcontainment')]
    panel_groups = [s for s in config.sections()
                    if re.fullmatch(r'Containments\]\[\d+', s)
                    and config.get(s, 'plugin', fallback='') == 'org.kde.panel']
    if not desktop_groups:
        s = f'Containments][{allocate()}'
        put(s, formfactor=0, location=0,
            lastScreen=0, wallpaperplugin='org.kde.image')
        desktop_groups.append(s)
    for s in desktop_groups:
        # A Desktop containment does not display the archived Desktop folder.
        put(s, plugin='org.kde.desktopcontainment', wallpaperplugin='org.kde.image')
        put(s + '][Wallpaper][org.kde.image][General',
            Image=Path(settings['wallpaper']).as_uri(),
            PreviewImage=Path(settings['wallpaper']).as_uri())
    if not panel_groups:
        s = f'Containments][{allocate()}'
        put(s, plugin='org.kde.panel', formfactor=2, location=4, lastScreen=0)
        order = []
        for plugin in ('org.kde.plasma.kickoff', 'org.kde.plasma.icontasks',
                       'org.kde.plasma.systemtray', 'org.kde.plasma.digitalclock'):
            widget = allocate()
            order.append(str(widget))
            put(s + f'][Applets][{widget}', plugin=plugin)
        put(s + '][General', AppletOrder=';'.join(order))
        panel_groups.append(s)
    for panel in panel_groups:
        managers = [s for s in config.sections()
                    if re.fullmatch(re.escape(panel) + r'\]\[Applets\]\[\d+', s)
                    and config.get(s, 'plugin', fallback='') in
                    ('org.kde.plasma.icontasks', 'org.kde.plasma.taskmanager')]
        if not managers:
            # Add, never remove unrelated widgets from an existing panel.
            widget = allocate()
            s = panel + f'][Applets][{widget}'
            put(s, plugin='org.kde.plasma.icontasks')
            order_group = panel + '][General'
            old = config.get(order_group, 'AppletOrder', fallback='')
            put(order_group, AppletOrder=';'.join(filter(None, [old, str(widget)])))
            managers.append(s)
        for manager in managers:
            group = manager + '][Configuration][General'
            if config.has_section(group):
                config.remove_option(group, 'launchers[$i]')
            put(group, launchers=','.join(settings['launchers']))
    buffer = io.StringIO()
    config.write(buffer, space_around_delimiters=False)
    return buffer.getvalue()


def check_personal_autostarts(config_home):
    # The systemd generator does not reliably see Plasma's later XDG_CONFIG_DIRS.
    # Use the standard user autostart directory and reload BEFORE Plasma starts.
    # Refuse unknown personal entries, symlinks and redirected directories.
    directory = config_home / 'autostart'
    if directory.is_symlink():
        raise ValueError(f'Autostart-Verzeichnis ist ein Symlink: {directory}')
    for name in AUTOSTART_NAMES:
        entry = config_home / 'autostart' / name
        if entry.is_symlink():
            raise ValueError(f'Autostart-Datei ist ein Symlink: {entry}')
        if entry.exists():
            text = entry.read_text()
            config = parser(text)
            hidden = config.get('Desktop Entry', 'Hidden', fallback='false').lower()
            managed = config.get('Desktop Entry', 'X-Cisco-Lab-Managed', fallback='false').lower()
            if managed != 'true' and not (name != 'lab-clock.desktop' and hidden == 'true'):
                raise ValueError(f'Persoenlicher konkurrierender Autostart: {entry}')


def prepare_autostarts(config_home, templates):
    """Copy only four known entries, as the user; never modify shared Files."""
    directory = config_home / 'autostart'
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    changed = False
    for name in AUTOSTART_NAMES:
        text = (templates / name).read_text()
        config = parser(text)
        config.set('Desktop Entry', 'X-Cisco-Lab-Managed', 'true')
        buffer = io.StringIO()
        config.write(buffer, space_around_delimiters=False)
        updated = buffer.getvalue()
        target = directory / name
        if target.is_file() and target.read_text() == updated:
            continue
        fd, temporary = tempfile.mkstemp(prefix='.lab-autostart-', dir=directory)
        try:
            with os.fdopen(fd, 'w') as stream:
                stream.write(updated)
            os.replace(temporary, target)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        changed = True
    return changed


def apply(config_home, settings):
    target = config_home / 'plasma-org.kde.plasma.desktop-appletsrc'
    if target.is_symlink():
        raise ValueError('Plasma-Konfiguration ist ein Symlink; keine Aenderung')
    original = target.read_text() if target.exists() else ''
    updated = render(original, settings)
    if updated == original:
        return False
    config_home.mkdir(parents=True, exist_ok=True)
    if target.exists():
        backups = config_home / 'cisco-lab-backups' / 'block01'
        backups.mkdir(parents=True, exist_ok=True, mode=0o700)
        shutil.copy2(target, backups / f'plasma-{time.time_ns()}.conf')
    fd, temporary = tempfile.mkstemp(prefix='.lab-plasma-', dir=config_home)
    try:
        with os.fdopen(fd, 'w') as stream:
            stream.write(updated)
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return True


def main():
    if os.geteuid() == 0:
        raise ValueError('Nicht als root ausfuehren')
    settings = json.loads(Path('/etc/cisco-lab/desktop.json').read_text())
    gid = grp.getgrnam(settings['group']).gr_gid
    if gid not in set(os.getgroups()) | {os.getgid()}:
        return 3  # not a LAB user; do not export LAB policy directories
    config_home = Path(os.environ.get('XDG_CONFIG_HOME', str(Path.home() / '.config')))
    check_personal_autostarts(config_home)
    apply(config_home, settings)
    prepare_autostarts(config_home, Path('/etc/cisco-lab/xdg/autostart'))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as error:
        print(f'LAB desktop preparation failed: {error}', file=sys.stderr)
        sys.exit(1)
