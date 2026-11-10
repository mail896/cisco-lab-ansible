"""Closed, read-only Teacher diagnostics; invoked only through a dedicated SSH key."""
import os
from pathlib import Path
import socket
import subprocess
import sys

ENDPOINT = '/usr/local/sbin/cisco-lab-teacher-diagnostics'
JOURNAL = ['/usr/bin/journalctl', '--no-pager', '--quiet', '-o', 'short-iso', '-n', '400']
COMMANDS = {
    'boots': [['/usr/bin/journalctl', '--list-boots', '--no-pager']],
    'previous': [JOURNAL + ['-b', '-1']],
    'current': [JOURNAL + ['-b', '0']],
    'kernel-previous': [JOURNAL + ['-b', '-1', '-k']],
    'kernel': [JOURNAL + ['-b', '0', '-k']],
    'user-previous': [JOURNAL + ['-b', '-1', '-u', 'user@*.service', '-u', 'systemd-logind.service', '-u', 'sddm.service']],
    'user': [JOURNAL + ['-b', '0', '-u', 'user@*.service', '-u', 'systemd-logind.service', '-u', 'sddm.service']],
    'services': [['/usr/bin/systemctl', '--failed', '--no-pager'],
                 ['/usr/bin/systemctl', 'list-jobs', '--no-pager']],
    'sessions': [['/usr/bin/loginctl', 'list-sessions', '--no-pager']],
    'processes': [['/usr/bin/ps', '-eo', 'pid,ppid,user,stat,wchan:30,comm', '--sort=pid']],
    'mounts': [['/usr/bin/findmnt', '-rn', '-o', 'TARGET,SOURCE,FSTYPE,OPTIONS']],
    'network': [['/usr/sbin/ip', '-br', 'address'], ['/usr/sbin/ip', 'route'], ['/usr/sbin/ip', '-6', 'route']],
    'boot-config': [['/usr/bin/cat', '/proc/cmdline', '/sys/kernel/reboot/type']],
}


def commands(name):
    if name not in COMMANDS:
        raise ValueError('Erlaubte Diagnosebefehle: ' + ', '.join(COMMANDS))
    return COMMANDS[name]


def main():
    if Path(sys.argv[0]).name.endswith('-ssh'):
        command = os.environ.get('SSH_ORIGINAL_COMMAND', '')
        commands(command)
        raise SystemExit(subprocess.run(['/usr/bin/sudo', '-n', ENDPOINT],
                                        input=command, text=True, timeout=90).returncode)
    if os.geteuid() != 0 or socket.gethostname().split('.')[0] != 'LAB-Teacher':
        raise SystemExit('Nur als root auf LAB-Teacher.')
    command = sys.stdin.read(201)
    for argv in commands(command):
        print('\n' + ' '.join(argv), flush=True)
        try:
            result = subprocess.run(argv, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    timeout=20, env={'PATH': '/usr/sbin:/usr/bin', 'LC_ALL': 'C.UTF-8',
                                                     'SYSTEMD_PAGER': 'cat', 'SYSTEMD_COLORS': '0'},
                                    stdin=subprocess.DEVNULL)
            print(result.stdout[:524288], end='')
            if result.returncode:
                print('\nDiagnosebefehl endete mit Status', result.returncode)
        except subprocess.TimeoutExpired:
            print('Diagnosebefehl nach 20 Sekunden abgebrochen.')
        except FileNotFoundError:
            print('Diagnosewerkzeug nicht vorhanden:', argv[0])


if __name__ == '__main__':
    main()
