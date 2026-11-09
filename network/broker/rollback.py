#!/usr/bin/python3
"""Root-only migration rollback; never exposed through the student socket."""
if __package__ in (None,''):
    import sys
    from pathlib import Path
    sys.path.insert(0,str(Path(__file__).resolve().parents[2]));__package__='network.broker'

import json
import os
from pathlib import Path
import subprocess
import sys
from network.broker.storage import trusted_file
from network.security.lab_request import validate_identity

UNITS={'lab-network-heal.timer','lab-school-network-heal.timer',
       'lab-network-heal.service','lab-school-network-heal.service','cisco-lab-network.service'}


def command(args):
    return subprocess.run(args,check=True,capture_output=True,text=True,timeout=60).stdout


def rollback(directory, run=command, read=trusted_file):
    directory=Path(directory)
    settings=json.loads(read(directory/'settings.json'))
    units=json.loads(read(directory/'units.json'))
    manifest=settings['manifest'];validate_identity(manifest['identity'])
    lab=manifest['identity']['lab']['uuid'];school=manifest['identity']['school']['uuid']
    # Ensure the complete restore source and file manifest exist before effects.
    source=read(directory/'source/role/files/snapshot.py')
    json.loads(read(directory/'files.json'))
    # Parse/validate all unit identities before stopping anything.
    states=[]
    for row in units:
        name=row['item']
        if name not in UNITS:raise ValueError('Unexpected unit in rollback manifest')
        state=dict(line.split('=',1) for line in row['stdout'].splitlines() if '=' in line)
        states.append((name,state))
    # This script is also saved before installation; stop a new service only
    # if it reached an active state. A failed service is safe to stop as well.
    service_state=run(['/usr/bin/systemctl','show','cisco-lab-network.service','-p','LoadState'])
    if 'LoadState=not-found' not in service_state:
        run(['/usr/bin/systemctl','stop','cisco-lab-network.service'])
    active=set(run(['/usr/bin/nmcli','-t','-f','UUID','connection','show','--active']).splitlines())
    if lab in active:run(['/usr/bin/nmcli','--wait','15','connection','down','uuid',lab])
    namespace={'__name__':'snapshot_restore'}
    exec(compile(source,'trusted-snapshot-restore','exec'),namespace)
    namespace['restore'](directory)
    run(['/usr/bin/systemctl','daemon-reload'])
    run(['/usr/bin/nmcli','general','reload','conf'])
    run(['/usr/bin/nmcli','connection','reload'])
    # School recovery first; reconnecting may interrupt the operator's SSH.
    run(['/usr/bin/nmcli','--wait','30','connection','up','uuid',school])
    if settings['verified']['active_lab']:
        run(['/usr/bin/nmcli','--wait','15','connection','up','uuid',lab])
    for name,state in states:
        if state.get('LoadState')=='not-found':continue
        if state.get('UnitFileState') in ('enabled','enabled-runtime'):
            args=['/usr/bin/systemctl','enable']
            if state['UnitFileState']=='enabled-runtime':args+=['--runtime']
            run(args+[name])
        if state.get('ActiveState') in ('active','activating'):
            run(['/usr/bin/systemctl','start',name])
    return {'ok':True,'note':'Recheck school route, DNS and user permissions before student use.'}


if __name__=='__main__':
    if os.geteuid()!=0 or len(sys.argv)!=2:raise SystemExit('Root and exact backup directory required')
    print(json.dumps(rollback(Path(sys.argv[1]))))
