"""Read-only Teacher facts for designing the restricted maintenance receiver."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess


def read(*command):
    result = subprocess.run(command, capture_output=True, text=True,
                            env=dict(os.environ, LC_ALL='C'), timeout=20)
    return result.returncode, result.stdout.strip()


def main():
    if socket.gethostname().split('.')[0] != 'LAB-Teacher':
        raise SystemExit('Dieser lesende Vorcheck ist ausschließlich für LAB-Teacher bestimmt.')
    release = dict(line.split('=', 1) for line in Path('/etc/os-release').read_text().splitlines()
                   if '=' in line)
    facts = {'host': 'LAB-Teacher', 'os': {key: release.get(key, '').strip('"')
                                         for key in ('ID', 'VERSION_ID')},
             'tools': {name: bool(shutil.which(name)) for name in
                       ('python3', 'sudo', 'systemctl', 'apt-get', 'loginctl')}}
    code, output = read('dpkg-query', '-W', '-f=${Package} ${Version}\n',
                        'python3', 'sudo', 'openssh-server', 'winbind', 'samba-libs', 'libpam-runtime')
    facts['packages'] = output.splitlines()
    facts['package_query_ok'] = code == 0
    facts['dpkg_audit_clear'] = read('dpkg', '--audit') == (0, '')
    facts['pam_nologin'] = {name: any('pam_nologin.so' in line and not line.lstrip().startswith('#')
                                    for line in Path('/etc/pam.d', name).read_text().splitlines())
                            for name in ('sddm', 'sshd', 'login')}
    facts['other_login_gate_present'] = any(path.exists() or path.is_symlink()
                                           for path in (Path('/run/nologin'), Path('/etc/nologin')))
    facts['units'] = {}
    for unit in ('apt-daily.service', 'apt-daily-upgrade.service',
                 'cisco-lab-teacher-wake.timer', 'sleep.target', 'suspend.target'):
        code, output = read('systemctl', 'show', unit, '-p', 'LoadState', '-p', 'ActiveState', '-p', 'UnitFileState')
        facts['units'][unit] = {'query_ok': code == 0, 'state': output.splitlines()}
    module = Path('/usr/lib/x86_64-linux-gnu/samba/idmap/ad.so')
    digest = hashlib.sha256(module.read_bytes()).hexdigest() if module.is_file() else None
    facts['winbind_module'] = {
        '61fe179121bcc6d3442d1ded9b59e9b5239daae4adc905ac6e487735d81b89e9': 'patched',
        'f7924c46f0ded38e5965fdc9f95a08e278fceedf02e3b29239b6400560b54943': 'stock',
    }.get(digest, 'unknown' if digest else 'absent')
    print(json.dumps(facts, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
