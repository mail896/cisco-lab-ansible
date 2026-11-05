#!/usr/bin/python3
"""Read-only deployment checks. No profile writes, connections or rules changed."""
if __package__ in (None,''):
    import sys
    from pathlib import Path
    sys.path.insert(0,str(Path(__file__).resolve().parents[2]));__package__='network.broker'

import ipaddress
import json
from pathlib import Path
import re
import subprocess
import sys
from network.broker.backend import Backend,NM,IP
from network.broker.service import load_config
from network.security.lab_request import keyfile
from network.security.native_keyfile import verify


def main(path):
    config=load_config(path)
    backend=Backend(config['identity'])
    version=backend.run([NM,'--version'])
    match=re.search(r'(\d+)\.(\d+)',version)
    if not match or tuple(map(int,match.groups()))<(1,52):
        raise ValueError('NetworkManager >= 1.52 fuer allowed-connections erforderlich.')
    # Reject graphical/TTY student or admin sessions, including inactive sessions.
    sessions=backend.run(['/usr/bin/loginctl','list-sessions','--no-legend'])
    for line in sessions.splitlines():
        text=backend.run(['/usr/bin/loginctl','show-session',line.split()[0],'-p','Type','-p','Class','-p','Remote'])
        fields=dict(row.split('=',1) for row in text.splitlines() if '=' in row)
        interactive=fields.get('Type') in ('x11','wayland') or (fields.get('Type')=='tty' and fields.get('Remote')=='no')
        if interactive and fields.get('Class','').startswith('user'):
            raise ValueError('Lokale Benutzersitzung vorhanden; Migration erst nach Abmeldung.')
    identity=backend.resolved()
    backend.school_guard(identity)
    if identity['school']['uuid'] not in backend.uuids(active=True):
        raise ValueError('Die zugeordnete Schul-UUID ist nicht aktiv.')
    # The manifest must cover actual school-connected/static route networks.
    protected=[ipaddress.ip_network(n) for n in identity['protected_subnets']]
    for family in ('-4','-6'):
        for route in json.loads(backend.run([IP,family,'-j','route','show','table','main'])):
            if route.get('dev')!=identity['school']['interface'] or route.get('dst','default')=='default':continue
            network=ipaddress.ip_network(route['dst'],strict=False)
            if not any(network.version==p.version and network.subnet_of(p) for p in protected):
                raise ValueError('Manifest schuetzt nicht alle vorhandenen Schulrouten: '+str(network))
    paths={kind:str(backend.profile_path(kind)) for kind in ('school','lab')}
    if not Path(paths['school']).exists():
        raise ValueError('Schulprofil muss vor der Testmigration eindeutig gespeichert sein.')
    # Local keyfile validation, never a connection to NetworkManager.
    verify(keyfile({'action':'clear','addresses':[],'routes':[]},identity))
    earlier=[]
    for directory in (Path('/etc/polkit-1/rules.d'),Path('/usr/share/polkit-1/rules.d')):
        for file in directory.glob('*.rules'):
            if file.name<'10-cisco-lab-network.rules':earlier.append(str(file))
    if earlier:
        raise ValueError('Fruehere Polkit-Regeln zuerst pruefen, um Vorrang zu klaeren: '+', '.join(sorted(earlier)))
    print(json.dumps({'identity':identity,'profile_paths':paths,'version':version.strip(),'active_lab':identity['lab']['uuid'] in backend.uuids(active=True)}))


if __name__=='__main__':
    if len(sys.argv)!=2:raise SystemExit('Exactly one root manifest path required')
    main(Path(sys.argv[1]))
