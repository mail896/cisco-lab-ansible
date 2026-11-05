#!/usr/bin/python3
"""Remove old settings favorites through KActivities, as the logged-in LAB user."""
import grp
import os
import subprocess


def main():
    if os.geteuid() == 0:
        return
    try:
        gid = grp.getgrnam('lab.students').gr_gid
    except KeyError:
        return
    if gid == 0 or gid not in {os.getgid(), *os.getgroups()}:
        return

    command = [
        '/usr/bin/busctl', '--user', '--timeout=4s', 'call',
        'org.kde.ActivityManager', '/ActivityManager/Resources/Linking',
        'org.kde.ActivityManager.ResourcesLinking',
        'UnlinkResourceFromActivity', 'sss',
        'org.kde.plasma.favorites.applications',
    ]
    for desktop_id in ('systemsettings.desktop', 'kdesystemsettings.desktop',
                       'org.kde.kinfocenter.desktop'):
        subprocess.run(
            command + ['applications:' + desktop_id, ':any'],
            check=True, timeout=5,
        )


if __name__ == '__main__':
    main()
