"""One inventory source for fixed LAB destinations; no Ansible process needed."""
from pathlib import Path
import re
import shlex

from dashboard.runtime import ROOT

HOSTS=tuple(f'LAB-{number:02d}{side}' for number in range(1,9) for side in 'AB')


def load_targets(path):
    group=None
    targets={}
    for line in Path(path).read_text().splitlines():
        fields=shlex.split(line,comments=True)
        if not fields:
            continue
        if len(fields)==1 and fields[0].startswith('[') and fields[0].endswith(']'):
            group=fields[0][1:-1]
            continue
        if group!='cisco_lab':
            continue
        if (len(fields)!=2 or fields[0] not in HOSTS or fields[0] in targets
                or not fields[1].startswith('ansible_host=')):
            raise ValueError('Inventar muss genau die benannten LAB-Schülerclients enthalten.')
        address=fields[1].removeprefix('ansible_host=')
        if not re.fullmatch(r'203\.0\.113\.(?:[1-9]|1[0-6])',address):
            raise ValueError('LAB-Zieladresse liegt außerhalb des freigegebenen Clientbereichs.')
        targets[fields[0]]=address
    if set(targets)!=set(HOSTS) or len(set(targets.values()))!=len(HOSTS):
        raise ValueError('Inventar muss 16 vollständige und eindeutige LAB-Ziele enthalten.')
    return {host:targets[host] for host in HOSTS}


# Public checkouts ship the example; production keeps its reviewed inventory.ini.
INVENTORY=ROOT/'inventory.ini'
LAB_TARGETS=load_targets(INVENTORY if INVENTORY.is_file() else ROOT/'inventory.example.ini')
PILOTS={host:LAB_TARGETS[host] for host in ('LAB-08A','LAB-08B')}
