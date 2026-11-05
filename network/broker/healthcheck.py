#!/usr/bin/python3
"""Root read-only readiness probe; not an alternative student mutation API."""
if __package__ in (None,''):
    import sys
    from pathlib import Path
    sys.path.insert(0,str(Path(__file__).resolve().parents[2]));__package__='network.broker'

import json
import os
from pathlib import Path
import stat
import time
from network.broker.backend import Backend
from network.broker.service import load_config,SOCKET
from network.broker.storage import trusted_file
from network.broker.auth import group_id


def main():
    if os.geteuid()!=0:raise PermissionError('Root-Pruefung erforderlich.')
    config=load_config();info=SOCKET.lstat()
    if not stat.S_ISSOCK(info.st_mode) or info.st_uid!=0 or info.st_gid!=group_id(config['group']) or stat.S_IMODE(info.st_mode)!=0o660:
        raise PermissionError('Socket fehlt oder hat unsichere Berechtigungen.')
    health=json.loads(trusted_file(Path('/var/lib/cisco-lab-network/health.json')))
    if health.get('ok') is not True or time.time()-health['time']>65:
        raise ValueError('Netzwerk-Heilung noch nicht fehlerfrei bereit.')
    Backend(config['identity']).health()
    print('Vermittler-Socket, Schulroute und Laborprofil bereit.')

if __name__=='__main__':main()
