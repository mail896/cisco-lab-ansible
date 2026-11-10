"""Read-only room maintenance preview. Never wakes, updates or reboots a client."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, time
import json
from pathlib import Path
import re
import subprocess
from zoneinfo import ZoneInfo

BERLIN = ZoneInfo('Europe/Berlin')
CLIENT_WORKERS = 16
LAB_HOST = re.compile(r'LAB-0[1-8][AB]\Z')
PROTECTED_PACKAGES = {
    'apt', 'dpkg', 'sudo', 'systemd', 'openssh-server', 'openssh-client',
    'network-manager', 'libnm0', 'iproute2', 'udev', 'sddm', 'xserver-xorg',
    'xserver-xorg-core', 'x11-xserver-utils', 'samba', 'wireshark',
    'firefox-esr', 'putty', 'minicom', 'konsole', 'unterrichtszeit',
    'packettracer', 'acl', 'libcap2-bin', 'python3', 'python3-pyqt5',
    'desktop-file-utils', 'nmap', 'traceroute', 'net-tools', 'numlockx',
    'kdialog', 'libwbclient0', 'winbind', 'krb5-user', 'cifs-utils',
    'libpam-mount', 'libpam-krb5', 'libpam-winbind', 'libnss-winbind',
    'policykit-1', 'polkitd',
}
PROTECTED_PREFIXES = (
    'iserv-', 'samba-', 'veyon-', 'wireshark-', 'libwireshark',
    'libwiretap', 'libwsutil', 'plasma-', 'kde-', 'sddm-',
)
VERSIONED_KERNEL = re.compile(r'linux-image-[0-9].*\Z')
PATCH_ABI_PACKAGES = {'winbind', 'samba-libs', 'samba-common-bin', 'libwbclient0'}
PATCH_HASH = '61fe179121bcc6d3442d1ded9b59e9b5239daae4adc905ac6e487735d81b89e9'
STOCK_HASH = 'f7924c46f0ded38e5965fdc9f95a08e278fceedf02e3b29239b6400560b54943'


def start_allowed(moment):
    if moment.utcoffset() is None:
        raise ValueError('Timestamp must have a timezone')
    local = moment.astimezone(BERLIN)
    return local.weekday() < 5 and time(18) <= local.time() < time(20)


def parse_apt(text):
    result = dict(install=[], remove=[])
    for line in text.splitlines():
        fields = line.split()
        if not fields or fields[0] not in {'Inst', 'Remv', 'Purg'}:
            continue
        if len(fields) < 2 or not re.fullmatch(r'[a-z0-9][a-z0-9+.-]*(?::[a-z0-9-]+)?', fields[1]):
            raise ValueError(f'Unbekannte APT-Ausgabe: {line}')
        category = 'install' if fields[0] == 'Inst' else 'remove'
        result[category].append(fields[1])
    return result


def package_name(value):
    return value.partition(':')[0]


def protected(value):
    name = package_name(value)
    return (name in PROTECTED_PACKAGES or name.startswith(PROTECTED_PREFIXES)
            or (name.startswith('linux-image-') and not VERSIONED_KERNEL.fullmatch(name)))


def plan_host(probe):
    if probe.get('error'):
        return dict(action='wol_candidate' if probe.get('wake_possible') else 'review',
                    shutdown_eligible=False, reason=probe['error'])
    if probe.get('dpkg_audit'):
        return dict(action='review', shutdown_eligible=False, reason='Unvollständiger dpkg-Zustand')
    upgrade = probe['upgrade']
    cleanup = probe['autoremove']
    blocked = {name for name in cleanup['remove'] if protected(name)}
    kernel_removals = {value for value in cleanup['remove']
                       if VERSIONED_KERNEL.fullmatch(package_name(value))}
    if kernel_removals:
        running = probe.get('running_kernel')
        remove_names = {package_name(value) for value in kernel_removals}
        bootable = set(probe.get('bootable_kernels', []))
        if running not in bootable or running in remove_names or not (bootable - remove_names - {running}):
            blocked.update(kernel_removals)
    abi_updates = sorted({name for name in upgrade['install'] if package_name(name) in PATCH_ABI_PACKAGES})
    patch = {PATCH_HASH: 'patched', STOCK_HASH: 'stock'}.get(probe.get('ad_hash'), 'unknown')
    if probe['occupied'] and not probe.get('evening_restart'):
        action = 'download_only'
    elif upgrade['remove'] or (abi_updates and patch != 'stock'):
        action = 'review'
    elif probe.get('evening_restart'):
        action = 'evening_logout_install'
    else:
        action = 'install_if_still_free'
    return dict(action=action, shutdown_eligible=False, patch=patch, abi_updates=abi_updates,
                upgrade=upgrade, autoremove=cleanup, blocked_removals=sorted(blocked),
                autoremove_allowed=action == 'install_if_still_free' and not blocked
                                   and not cleanup['install'],
                reboot_required=probe['reboot_required'],
                automatic_apt=probe.get('automatic_apt'))


# Runs with the existing SSH user. No Ansible remote module transfer or sudo.
PROBE = r'''
import hashlib, json, os, re, socket, subprocess, sys
from pathlib import Path

if socket.gethostname().split('.')[0] != sys.argv[1]:
    raise SystemExit('SSH-Ziel meldet unerwarteten Rechnernamen.')
def read(*argv):
    return subprocess.check_output(argv, text=True, stderr=subprocess.PIPE,
                                   env=dict(os.environ, LC_ALL='C'), timeout=20)
scope = re.search(r'/session-([a-zA-Z0-9]+)\.scope(?:/|$)',
                  Path('/proc/self/cgroup').read_text(), re.MULTILINE)
own_session = scope.group(1) if scope else None
occupied = False
for row in read('loginctl', 'list-sessions', '--no-legend').splitlines():
    session = row.split()[0]
    if session == own_session:
        continue
    kind = read('loginctl', 'show-session', session, '-p', 'Class', '--value').strip()
    if not kind:
        raise SystemExit('Sitzungsstatus nicht eindeutig.')
    occupied |= kind.startswith('user')
mounts = read('findmnt', '-n', '-o', 'TARGET').splitlines()
occupied |= any(line.startswith('/var/lib/iserv/client/home/') for line in mounts)
module = Path('/usr/lib/x86_64-linux-gnu/samba/idmap/ad.so')
with module.open('rb') as handle:
    checksum = hashlib.file_digest(handle, 'sha256').hexdigest()
running_kernel = 'linux-image-' + os.uname().release
bootable_kernels = []
packages = read('dpkg-query', '-W', '-f=${Package} ${Status}\n', 'linux-image-*')
for line in packages.splitlines():
    name, _, status = line.partition(' ')
    if status != 'install ok installed' or not re.fullmatch(r'linux-image-[0-9].*', name):
        continue
    release = name.removeprefix('linux-image-')
    if all(Path('/boot', prefix + release).is_file() for prefix in ('vmlinuz-', 'initrd.img-')):
        bootable_kernels.append(name)
periodic = read('apt-config', 'dump')
unattended = re.search(r'^APT::Periodic::Unattended-Upgrade "([^"]+)";$', periodic, re.MULTILINE)
timer = subprocess.run(['systemctl', 'is-enabled', 'apt-daily-upgrade.timer'],
                       text=True, capture_output=True, timeout=10)
automatic_apt = dict(periodic_unattended=unattended.group(1) if unattended else None,
                     upgrade_timer=timer.stdout.strip() or 'unknown')
maintenance = subprocess.run(['systemctl', 'show', 'cisco-lab-maintenance.service', '-p', 'ExecStart', '--value'],
                             text=True, capture_output=True, timeout=10)
evening_restart = maintenance.returncode == 0 and '--evening-restart' in maintenance.stdout.split()
print(json.dumps(dict(occupied=occupied, evening_restart=evening_restart, automatic_apt=automatic_apt, running_kernel=running_kernel, bootable_kernels=bootable_kernels, dpkg_audit=bool(read('dpkg', '--audit').strip()),
                      upgrade=read('apt-get', '-s', '--with-new-pkgs', 'upgrade'),
                      autoremove=read('apt-get', '-s', 'autoremove'),
                      ad_hash=checksum,
                      reboot_required=Path('/run/reboot-required').exists())))
'''


def pinned_hosts_file(directory, host):
    if not LAB_HOST.fullmatch(host):
        raise ValueError('Unbekannter LAB-Client.')
    if directory is None:
        return None
    pin = directory / (host + '.active.known_hosts')
    if pin.exists() or pin.is_symlink():
        if pin.is_symlink() or not pin.is_file() or pin.stat().st_mode & 0o077:
            raise ValueError('Die verifizierte SSH-Hostdatei ist nicht geschützt.')
        return pin
    return None


def probe_host(name, address, key, known_hosts, pin_directory=None):
    try:
        pin = pinned_hosts_file(pin_directory, name) or (known_hosts if name == 'LAB-08B' else None)
        command = ['ssh', '-F', '/dev/null', '-o', 'BatchMode=yes', '-o', 'IdentitiesOnly=yes',
                   '-o', 'StrictHostKeyChecking=yes', '-o', 'ConnectTimeout=4', '-i', str(key)]
        if pin:
            command.extend(['-o', f'UserKnownHostsFile={pin}', '-o', 'GlobalKnownHostsFile=/dev/null'])
        command.extend([f'administrator@{address}', f'/usr/bin/python3 -IB - {name}'])
        result = subprocess.run(command, input=PROBE, text=True, capture_output=True, timeout=65)
        if result.returncode:
            details = result.stderr.strip()
            return dict(error=details or f'SSH-/Prüffehler {result.returncode}',
                        wake_possible=details.startswith('ssh: connect to host') and any(
                            message in details for message in ('Connection timed out', 'No route to host')))
        result_data = json.loads(result.stdout)
        for name in ('upgrade', 'autoremove'):
            result_data[name] = parse_apt(result_data[name])
        return result_data
    except (subprocess.SubprocessError, ValueError, OSError) as error:
        return dict(error=str(error))


def main():
    from dashboard.targets import load_targets
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inventory', type=Path, default=Path('inventory.ini'))
    parser.add_argument('--key', type=Path, default=Path.home() / '.ssh/id_ed25519')
    parser.add_argument('--lab08b-known-hosts', type=Path)
    parser.add_argument('--pin-directory', type=Path)
    args = parser.parse_args()
    inventory=load_targets(args.inventory)
    hosts=list(inventory)
    addresses=list(inventory.values())
    with ThreadPoolExecutor(max_workers=CLIENT_WORKERS) as executor:
        probes = executor.map(
            lambda target: probe_host(*target, args.key, args.lab08b_known_hosts, args.pin_directory),
            zip(hosts, addresses),
        )
        room = {host: plan_host(probe) for host, probe in zip(hosts, probes)}
    now = datetime.now(BERLIN)
    print(json.dumps(dict(timestamp=now.isoformat(), may_start_now=start_allowed(now),
                          clients=room, teacher='after_clients'), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
