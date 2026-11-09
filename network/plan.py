#!/usr/bin/python3
"""Offline change planner. Produces data only; never invokes nmcli/system tools."""
import argparse
import json
from pathlib import Path
import re
import uuid


def build_plan(facts):
    if facts['host'] != 'LAB-08A':
        raise ValueError('Only LAB-08A may be planned; comparison host is excluded')
    school, lab = facts['school'], facts['lab']
    for data in (school, lab):
        if not re.fullmatch(r'[0-9a-fA-F]{2}(?::[0-9a-fA-F]{2}){5}', data['mac']):
            raise ValueError('Invalid verified MAC address')
        if not re.fullmatch(r'[a-zA-Z0-9_.-]+', data['interface']):
            raise ValueError('Invalid interface')
        uuid.UUID(data['uuid'])
    if school['usb'] or not lab['usb']:
        raise ValueError('School must be internal; lab must be USB, verified using sysfs')
    if school['mac'].lower() == lab['mac'].lower() or school['interface'] == lab['interface'] or school['uuid'] == lab['uuid']:
        raise ValueError('School and laboratory must be distinct')
    return {
        'executable': False,
        'school_identity': school,
        'lab_identity': lab,
        'steps': [
            'Save NM profiles, routes, permissions and both old timer states',
            'Verify authorization denies school changes and allows lab changes',
            'Replace both heal timers with one, preserving school DHCP/default route',
            'Restore missing lab profile without overwriting existing exercise addresses',
            'Reset only lab profile before student login: no DHCP, no addresses/routes',
            'Keep lab link available; require manual IPv4/IPv6 setup before activation',
            'Use never-default on lab; protect school DNS and routing priority',
            'Test deletion, USB reconnect, login, school failure and rollback locally'
        ],
        'blockers': ['Physical IServ/Polkit authorization acceptance is still required',
                     'Reviewed per-host manifest and physical recovery/rollback test are required']
    }


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('facts', type=Path, help='Local JSON of verified identities; no host probing')
    args = parser.parse_args()
    print(json.dumps(build_plan(json.loads(args.facts.read_text())), indent=2))
