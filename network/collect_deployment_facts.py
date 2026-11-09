#!/usr/bin/python3
"""Read local hardware/NM metadata only. Never probes other machines or changes NM."""
if __package__ in (None,''):
    import sys
    from pathlib import Path
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]));__package__='network'
import json
import socket
import subprocess
from network.collect_interfaces import collect


def run(args):
    result=subprocess.run(args,capture_output=True,text=True,timeout=10,
                          env={'PATH':'/usr/sbin:/usr/bin:/sbin:/bin','LC_ALL':'C'})
    if result.returncode:raise ValueError('Lesende Abfrage fehlgeschlagen: '+args[0])
    return result.stdout


def main():
    profiles=[]
    rows=run(['/usr/bin/nmcli','--escape','no','-t','-f','UUID,TYPE,DEVICE','connection','show'])
    for line in rows.splitlines():
        identifier,kind,device=line.split(':',2)
        profiles.append({'uuid':identifier,'type':kind,'device':device})
    return {'host':socket.gethostname(),'interfaces':collect(),'profiles':profiles,
            'routes4':json.loads(run(['/usr/sbin/ip','-4','-j','route','show','table','main'])),
            'routes6':json.loads(run(['/usr/sbin/ip','-6','-j','route','show','table','main'])),
            'nm_version':run(['/usr/bin/nmcli','--version']).strip()}

if __name__=='__main__':print(json.dumps(main(),indent=2))
