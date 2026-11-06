#!/usr/bin/env python3
"""Start the password bootstrap and LAB baseline as one guided client operation."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess


REPOSITORY = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('host', help='Single inventory name, for example LAB-01A')
    parser.add_argument('variables', type=Path, help='Local JSON with reviewed hardware and artifacts')
    parser.add_argument('--key', type=Path, required=True, help='Management SSH identity on LAB-Teacher')
    parser.add_argument('--dashboard', action='store_true',
                        help='After SSH bootstrap, admit this client once for the dashboard')
    parser.add_argument('--plan', action='store_true', help='Show both selected targets without SSH')
    args = parser.parse_args()
    variables = args.variables.expanduser().resolve()
    identity_file = args.key.expanduser().resolve()

    try:
        settings = json.loads(variables.read_text())
        manifest = settings['lab_network_manifest']
        known_hosts = Path(settings['lab_fresh_known_hosts']).expanduser().resolve()
        school_mac = manifest['identity']['school']['mac']
        rebuild_08b = settings.get('lab_fresh_rebuild_08b') is True
        if not (re.fullmatch(r'LAB-0[1-8][AB]', args.host)
                and settings['lab_fresh_host'] == manifest['host'] == args.host
                and settings['lab_fresh_confirmed'] is True
                and (args.host != 'LAB-08B' or rebuild_08b)):
            raise ValueError('Host, manifest and explicit target selection must agree.')
        if not re.fullmatch(r'(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}', school_mac):
            raise ValueError('A verified school adapter MAC is required.')
        if not known_hosts.is_file():
            raise ValueError('Provide the locally verified SSH host key file.')
        if not identity_file.is_file() or not Path(f'{identity_file}.pub').is_file():
            raise ValueError('Provide the local SSH private and public key pair.')
    except (KeyError, TypeError, ValueError, OSError) as error:
        parser.error(str(error))

    command = ['ansible-playbook', '-i', 'inventory.ini', '--limit', args.host]
    if args.plan:
        for playbook in ('bootstrap-client.yml', 'fresh-client.yml'):
            subprocess.run(command + ['--list-hosts', playbook], cwd=REPOSITORY, check=True)
        return

    bootstrap_settings = dict(
        lab_bootstrap_host=args.host,
        lab_bootstrap_school_mac=school_mac,
        lab_bootstrap_identity_file=str(identity_file),
        lab_bootstrap_known_hosts=str(known_hosts),
        lab_bootstrap_rebuild_08b=rebuild_08b,
    )
    subprocess.run(command + ['bootstrap-client.yml', '--ask-pass', '--ask-become-pass',
                             '-e', json.dumps(bootstrap_settings)], cwd=REPOSITORY, check=True)
    if args.dashboard:
        directory = REPOSITORY / 'local-private/fresh-clients'
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        target = directory / f'{args.host}.json'
        settings['lab_fresh_known_hosts'] = str(known_hosts)
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, 'w') as file:
            json.dump(settings, file, indent=2)
            file.write('\n')
        print(f'{args.host} kann jetzt einmalig unter Neuinstallation gestartet werden.')
        return

    subprocess.run(command + ['fresh-client.yml', '--private-key', str(identity_file),
                             '-e', f'@{variables}', '-e',
                             json.dumps({'lab_fresh_known_hosts': str(known_hosts)})],
                   cwd=REPOSITORY, check=True)


if __name__ == '__main__':
    try:
        main()
    except subprocess.CalledProcessError as error:
        raise SystemExit(error.returncode)
