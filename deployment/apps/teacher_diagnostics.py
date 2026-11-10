"""Read Teacher diagnostics with the separate local key, never the web service key."""
import argparse
from pathlib import Path
import subprocess
from deployment.teacher.diagnostics import COMMANDS

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=COMMANDS)
    args = parser.parse_args()
    private = ROOT / 'local-private/teacher-diagnostics'
    result = subprocess.run([
        '/usr/bin/ssh', '-T', '-F', '/dev/null', '-o', 'BatchMode=yes',
        '-o', 'IdentitiesOnly=yes', '-o', 'IdentityAgent=none', '-o', 'ConnectTimeout=5',
        '-o', 'StrictHostKeyChecking=yes', '-o', 'GlobalKnownHostsFile=/dev/null',
        '-o', 'UserKnownHostsFile=' + str(private / 'known_hosts'),
        '-i', str(private / 'id_ed25519'), 'cisco-lab-app@203.0.113.100', args.command,
    ], timeout=95)
    raise SystemExit(result.returncode)


if __name__ == '__main__':
    main()
