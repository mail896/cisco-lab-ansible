"""Read-only collector sent inline to the selected fresh LAB client."""
import ipaddress
import json
from pathlib import Path
import socket
import subprocess
import sys
import uuid


class DeviceError(ValueError):
    """Stable error code; APP supplies the user-facing explanation."""


def command(*args):
    return subprocess.check_output(args, text=True, timeout=12).strip()


def protected_networks(routes):
    """Protect school-connected routes, without reserving arbitrary exercise ranges."""
    protected = {'203.0.113.0/24'}
    for route in routes:
        destination = route.get('dst', 'default')
        if destination == 'default':
            continue
        network = ipaddress.ip_network(destination, strict=False)
        # Link-local prefixes belong independently to each interface.
        if network.prefixlen and not network.is_link_local:
            protected.add(str(network))
    return sorted(protected)


def collect(host, address):
    if socket.gethostname().split('.')[0] != host:
        raise DeviceError('hostname')
    release = Path('/etc/os-release').read_text()
    if 'ID=debian\n' not in release or 'VERSION_ID="13"' not in release:
        raise DeviceError('image')
    addresses = json.loads(command('/usr/sbin/ip', '-j', 'address', 'show'))
    school = [row['ifname'] for row in addresses
              if any(item.get('local') == address for item in row.get('addr_info', []))]
    if len(school) != 1:
        raise DeviceError('school-address')
    defaults = json.loads(command('/usr/sbin/ip', '-j', '-4', 'route', 'show', 'default'))
    if not defaults or any(row.get('dev') != school[0] for row in defaults):
        raise DeviceError('school-route')
    active = {}
    for line in command('/usr/bin/nmcli', '-t', '-f', 'UUID,DEVICE', 'connection', 'show', '--active').splitlines():
        key, _, device = line.partition(':')
        active.setdefault(device, []).append(key)
    profiles = []
    for key in command('/usr/bin/nmcli', '-t', '-f', 'UUID', 'connection', 'show').splitlines():
        fields = subprocess.check_output(
            ['/usr/bin/nmcli', '--escape', 'no', '-g', 'connection.interface-name,802-3-ethernet.mac-address',
             'connection', 'show', 'uuid', key], text=True, timeout=8).splitlines()
        if len(fields) >= 2:
            profiles.append((key, fields[0], fields[1].lower()))
    adapters = []
    for path in sorted(Path('/sys/class/net').iterdir()):
        if (not (path / 'device').exists() or (path / 'wireless').exists()
                or (path / 'type').read_text().strip() != '1'):
            continue
        usb = any(part.startswith('usb') for part in path.resolve().parts)
        mac = (path / 'address').read_text().strip().lower()
        keys = active.get(path.name, []) or [key for key, device, bind in profiles
                                           if device == path.name or bind == mac]
        keys = sorted(set(keys))
        if len(keys) > 1:
            raise DeviceError('profiles')
        if path.name == school[0] and (usb or not active.get(path.name)):
            raise DeviceError('school-profile')
        adapters.append(dict(interface=path.name, mac=mac, usb=usb,
                             uuid=keys[0] if keys else str(uuid.uuid4()),
                             school=path.name == school[0]))
    if not any(row['usb'] for row in adapters):
        raise DeviceError('usb-adapter')
    school_routes = []
    for family in ('-4', '-6'):
        school_routes.extend(json.loads(command('/usr/sbin/ip', '-j', family, 'route', 'show', 'dev', school[0])))
    return dict(host=host, address=address, adapters=adapters, protected=protected_networks(school_routes))


if __name__ == '__main__':
    try:
        result = dict(ok=True, device=collect(sys.argv[1], sys.argv[2]))
    except DeviceError as error:
        result = dict(ok=False, code=str(error))
    except (OSError, ValueError, subprocess.SubprocessError):
        result = dict(ok=False, code='read-tools')
    print('CISCO_LAB_DEVICE_V1:' + json.dumps(result, separators=(',', ':')))
