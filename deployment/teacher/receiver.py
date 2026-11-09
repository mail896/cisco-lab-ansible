"""Closed Teacher maintenance receiver. Installed root-owned; never accepts shell text."""
from contextlib import contextmanager
from datetime import datetime, timedelta
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import stat
import subprocess
import sys
import tempfile
import time
import uuid

from maintenance.preview import (BERLIN, PATCH_ABI_PACKAGES, PATCH_HASH, STOCK_HASH,
                                 VERSIONED_KERNEL, package_name, parse_apt, plan_host, start_allowed)

STATE = Path('/var/lib/cisco-lab-teacher')
RUNTIME = Path('/run/cisco-lab-teacher-maintenance')
GATE = Path('/run/nologin')
MODULE = Path('/usr/lib/x86_64-linux-gnu/samba/idmap/ad.so')
UNIT = 'cisco-lab-teacher-maintenance.service'
ENV = dict(PATH='/usr/sbin:/usr/bin:/sbin:/bin', HOME='/root', LC_ALL='C',
           DEBIAN_FRONTEND='noninteractive', NEEDRESTART_MODE='l')
ACCOUNT = 'cisco-lab-app'


def require_teacher():
    if os.geteuid() != 0 or socket.gethostname().split('.')[0] != 'LAB-Teacher':
        raise RuntimeError('Empfänger ausschließlich als root auf LAB-Teacher.')


def read(*args):
    return subprocess.check_output(args, text=True, env=ENV, timeout=20).strip()


def boot_id():
    return Path('/proc/sys/kernel/random/boot_id').read_text().strip()


def load(name):
    path = STATE / (name + '.json')
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    with os.fdopen(descriptor) as file:
        info = os.fstat(file.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077 or info.st_size > 16384:
            raise RuntimeError('Teacher-Wartungszustand nicht privat und eindeutig.')
        value = json.load(file)
    if not isinstance(value, dict):
        raise ValueError('Ungültiger Teacher-Wartungszustand.')
    return value


def save(name, data):
    with tempfile.NamedTemporaryFile(mode='w', prefix='.teacher-', dir=STATE, delete=False) as file:
        path = Path(file.name)
        try:
            json.dump(data, file, ensure_ascii=False)
            file.flush(); os.fsync(file.fileno())
            path.replace(STATE / (name + '.json'))
        finally:
            path.unlink(missing_ok=True)


@contextmanager
def lock(name):
    descriptor = os.open(STATE / (name + '.lock'), os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'r+b') as file:
        info = os.fstat(file.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077:
            raise RuntimeError('Ungültige Teacher-Wartungssperre.')
        fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def occupied():
    for row in read('loginctl', 'list-sessions', '--no-legend').splitlines():
        fields = dict(line.split('=', 1) for line in read(
            'loginctl', 'show-session', row.split()[0], '-p', 'Class', '-p', 'Name',
            '-p', 'Service', '-p', 'Remote', '-p', 'Type').splitlines())
        if not fields.get('Class'):
            raise RuntimeError('Teacher-Sitzung nicht eindeutig.')
        # Remote SSH sessions do not count as local workstation occupancy.
        # Local consoles and graphical sessions still block, as do RAM homes.
        remote_ssh = (fields.get('Service') == 'sshd' and fields.get('Remote') == 'yes'
                      and fields.get('Type') not in ('x11', 'wayland'))
        if fields['Class'].startswith('user') and not remote_ssh:
            return True
    return any(line.startswith('/var/lib/iserv/client/home/')
               for line in read('findmnt', '-n', '-o', 'TARGET').splitlines())


def active():
    return read('systemctl', 'show', UNIT, '-p', 'ActiveState', '--value')


def apt_idle():
    return all(read('systemctl', 'show', unit, '-p', 'ActiveState', '--value') in ('inactive', 'failed')
               for unit in ('apt-daily.service', 'apt-daily-upgrade.service'))


def package_locks_idle():
    """Check the same advisory locks used by apt/dpkg, including manual SSH runs."""
    for path in (Path('/var/lib/dpkg/lock-frontend'), Path('/var/lib/dpkg/lock'),
                 Path('/var/lib/apt/lists/lock'), Path('/var/cache/apt/archives/lock')):
        try:
            descriptor = os.open(path, os.O_RDWR | os.O_CLOEXEC)
        except FileNotFoundError:
            continue
        with os.fdopen(descriptor, 'r+b') as file:
            try:
                fcntl.lockf(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return False
            finally:
                try:
                    fcntl.lockf(file, fcntl.LOCK_UN)
                except OSError:
                    pass
    return True


def automatic_installs_disabled():
    config = read('apt-config', 'dump')
    return bool(re.search(r'^APT::Periodic::(?:Enable|Unattended-Upgrade) "0";$', config, re.MULTILINE))


def session_diagnostics():
    """Bounded read-only evidence; never return command arguments or unknown usernames."""
    own = re.search(r'(?:^|/)session-([A-Za-z0-9]+)\.scope(?:/|$)', Path('/proc/self/cgroup').read_text())
    sessions = []
    rows = read('loginctl', 'list-sessions', '--no-legend').splitlines()
    if len(rows) > 64:
        raise RuntimeError('Zu viele Teacher-Sitzungen für die begrenzte Diagnose.')
    properties = ('Id', 'Name', 'Class', 'Service', 'Remote', 'State', 'Type', 'TTY', 'Leader')
    for row in rows:
        identity = row.split()[0]
        if not re.fullmatch('[A-Za-z0-9]+', identity):
            raise RuntimeError('Teacher-Sitzungskennung nicht eindeutig.')
        command = ['loginctl', 'show-session', identity]
        for key in properties:
            command.extend(['-p', key])
        fields = dict(line.split('=', 1) for line in read(*command).splitlines() if '=' in line)
        account = fields.pop('Name', '')
        fields['account'] = account if account in (ACCOUNT, 'marc', 'root', 'sddm') else 'other'
        fields['receiver_session'] = bool(own and identity == own.group(1))
        sessions.append(fields)
    process = subprocess.run(['ps', '-u', 'marc', '-o', 'pid=,ppid=,tty=,stat=,comm='],
                             text=True, capture_output=True, env=ENV, timeout=20)
    if process.returncode not in (0, 1):
        raise RuntimeError('Teacher-Prozessübersicht nicht lesbar.')
    processes = []
    lines = process.stdout.splitlines()
    for line in lines[:100]:
        fields = line.split(None, 4)
        if len(fields) == 5 and fields[0].isdigit() and fields[1].isdigit():
            processes.append(dict(pid=int(fields[0]), parent=int(fields[1]), tty=fields[2],
                                  state=fields[3], command=fields[4][:64]))
    return dict(sessions=sessions, operator_processes=processes, processes_truncated=len(lines) > 100)


def check():
    digest = hashlib.sha256(MODULE.read_bytes()).hexdigest()
    return dict(status(), occupancy_policy='local-sessions-and-ram-homes',
                automatic_installs_disabled=automatic_installs_disabled(),
                dpkg_audit_clear=not read('dpkg', '--audit'),
                winbind_module={PATCH_HASH: 'patched', STOCK_HASH: 'stock'}.get(digest, 'unknown'),
                sleep_targets={name: read('systemctl', 'show', name, '-p', 'LoadState', '--value')
                               for name in ('sleep.target', 'suspend.target')},
                diagnostics=session_diagnostics())


def cleanup_gate():
    if GATE.is_symlink() and GATE.readlink() == RUNTIME / 'nologin':
        GATE.unlink()


@contextmanager
def login_gate():
    if Path('/etc/nologin').exists():
        raise RuntimeError('Andere Teacher-Anmeldesperre vorhanden.')
    for service in ('sddm', 'sshd', 'login'):
        if not any('pam_nologin.so' in line and not line.lstrip().startswith('#')
                   for line in Path('/etc/pam.d', service).read_text().splitlines()):
            raise RuntimeError('Teacher-Anmeldeschutz fehlt.')
    marker = RUNTIME / 'nologin'
    marker.write_text('LAB-Teacher wird gewartet. Bitte nach Abschluss erneut anmelden.\n')
    marker.chmod(0o644)
    GATE.symlink_to(marker)
    try:
        for _ in range(3):
            time.sleep(2)
            if occupied():
                raise RuntimeError('Teacher-Sitzung vorhanden. Keine automatische Abmeldung.')
        yield
    finally:
        cleanup_gate()


def status():
    return dict(version=1, host='LAB-Teacher', boot_id=boot_id(),
                uptime=float(Path('/proc/uptime').read_text().split()[0]),
                active=active(), occupied=occupied(), capabilities=['manual-poweroff'],
                request=load('request'), result=load('result'))


def validate_arguments(text):
    if not isinstance(text, str) or len(text) > 200:
        raise ValueError('Ungültiger Teacher-Auftrag.')
    parts = text.split(' ')
    if parts in (['status'], ['check']):
        return parts
    if (len(parts) not in (3, 4) or parts[0] not in ('start', 'pilot', 'poweroff', 'manual-poweroff')
            or not re.fullmatch('[0-9a-f]{32}', parts[1])
            or str(uuid.UUID(parts[2])) != parts[2]
            or (parts[0] in ('poweroff', 'manual-poweroff') and len(parts) != 3)
            or (parts[0] not in ('poweroff', 'manual-poweroff')
                and (len(parts) != 4 or parts[3] not in ('woke', 'already-on')))):
        raise ValueError('Nur feste Teacher-Wartungsaufträge erlaubt.')
    return parts


def dispatch(text, *, acknowledge=None):
    require_teacher()
    parts = validate_arguments(text)
    if parts == ['status']:
        return status()
    if parts == ['check']:
        return check()
    action, request_id, expected_boot = parts[:3]
    with lock('endpoint'):
        if (boot_id() != expected_boot or occupied() or not apt_idle() or not package_locks_idle()
                or active() not in ('inactive', 'failed')):
            raise RuntimeError('Teacher belegt, beschäftigt oder Boot-ID geändert. Kein Start/Abschluss.')
        request, result = load('request'), load('result')
        if action == 'manual-poweroff':
            RUNTIME.mkdir(mode=0o755, exist_ok=True)
            with login_gate():
                response = {'poweroff_requested': True, 'id': request_id}
                if acknowledge is not None:
                    acknowledge(response)
                    time.sleep(1)
                subprocess.run(['systemctl', '--no-block', 'poweroff'], check=True, timeout=15)
            return response
        if action == 'poweroff':
            if (not request or not result or request.get('id') != request_id or result.get('id') != request_id
                    or result.get('status') != 'success' or request.get('woke') is not True
                    or result.get('boot_id') != request.get('boot_id')
                    or not timedelta(0) <= datetime.now(BERLIN) - datetime.fromisoformat(result['time']) <= timedelta(hours=4)
                    or result.get('poweroff_boot_id') is not None
                    or (result.get('reboot_required') and result.get('boot_id') == expected_boot)
                    or (not result.get('reboot_required') and request.get('boot_id') != expected_boot)):
                raise ValueError('Kein bestätigter eigener Teacher-Wartungsstart für das Ausschalten.')
            RUNTIME.mkdir(mode=0o755, exist_ok=True)
            with login_gate():
                result['poweroff_boot_id'] = expected_boot
                save('result', result)
                response = {'poweroff_requested': True, 'id': request_id}
                if acknowledge is not None:
                    # Send acceptance while SSH is still alive. Shutdown can
                    # terminate sshd before the command's normal exit status.
                    acknowledge(response)
                    time.sleep(1)
                subprocess.run(['systemctl', '--no-block', 'poweroff'], check=True, timeout=15)
            return response
        if action == 'start' and not start_allowed(datetime.now(BERLIN)):
            raise ValueError('Teacher-Start nur im Abendfenster.')
        if request and (not result or result.get('id') != request.get('id')
                        or result.get('status') not in ('success', 'failed', 'deferred')):
            raise RuntimeError('Ein früherer Teacher-Auftrag ist noch ungeklärt.')
        data = dict(id=request_id, boot_id=expected_boot, woke=parts[3] == 'woke',
                    manual_pilot=action == 'pilot', time=datetime.now(BERLIN).isoformat())
        save('request', data)
        save('result', dict(id=request_id, status='pending', boot_id=expected_boot))
        try:
            subprocess.run(['systemctl', 'start', '--no-block', UNIT], check=True, timeout=15)
        except BaseException:
            save('result', dict(id=request_id, status='failed', boot_id=expected_boot,
                                detail='Systemd-Start fehlgeschlagen.'))
            raise
        return {'started': True, 'id': request_id}


def apt(*args):
    subprocess.run(['apt-get', '-o', 'DPkg::Lock::Timeout=30',
                    '-o', 'Dpkg::Options::=--force-confdef', '-o', 'Dpkg::Options::=--force-confold',
                    *args], env=ENV, check=True)


def bootable_kernels():
    result = []
    for line in read('dpkg-query', '-W', '-f=${Package} ${Status}\n', 'linux-image-*').splitlines():
        package, _, state = line.partition(' ')
        release = package.removeprefix('linux-image-')
        if state == 'install ok installed' and VERSIONED_KERNEL.fullmatch(package) and all(
                Path('/boot', prefix + release).is_file() for prefix in ('vmlinuz-', 'initrd.img-')):
            result.append(package)
    return result


def maintain(request):
    if occupied() or not apt_idle() or read('dpkg', '--audit') or not automatic_installs_disabled():
        raise RuntimeError('Teacher-Sitzung, parallele Paketwartung oder unvollständiger dpkg-Zustand.')
    digest = hashlib.sha256(MODULE.read_bytes()).hexdigest()
    if digest not in (PATCH_HASH, STOCK_HASH):
        raise RuntimeError('Teacher-Winbind-Modul ist nicht der geprüfte Stand.')
    with login_gate():
        apt('-o', 'APT::Update::Error-Mode=any', 'update')
        upgrade = parse_apt(read('apt-get', '-s', '--with-new-pkgs', 'upgrade'))
        abi = any(package_name(name) in PATCH_ABI_PACKAGES for name in upgrade['install'])
        if upgrade['remove'] or (abi and digest != STOCK_HASH):
            raise RuntimeError('Teacher-Upgrade entfernt Pakete oder ändert die gepatchte Winbind-ABI.')
        if occupied():
            raise RuntimeError('Teacher wurde belegt. Keine Installation.')
        if upgrade['install']:
            apt('-y', '--no-remove', '--with-new-pkgs', 'upgrade')
        cleanup = parse_apt(read('apt-get', '-s', 'autoremove'))
        bootable = bootable_kernels()
        running = 'linux-image-' + os.uname().release
        decision = plan_host(dict(occupied=occupied(), dpkg_audit=bool(read('dpkg', '--audit')),
                                  upgrade={'install': [], 'remove': []}, autoremove=cleanup,
                                  ad_hash=digest, running_kernel=running,
                                  bootable_kernels=bootable, reboot_required=False))
        if cleanup['remove'] and decision['autoremove_allowed']:
            exact = parse_apt(read('apt-get', '-s', '--no-auto-remove', 'remove', *cleanup['remove']))
            if set(exact['remove']) != set(cleanup['remove']) or exact['install'] or occupied():
                raise RuntimeError('Teacher-Paketbereinigung weicht von der geprüften Liste ab.')
            apt('-y', '--no-auto-remove', 'remove', *cleanup['remove'])
        if read('dpkg', '--audit') or occupied():
            raise RuntimeError('Teacher-Nachprüfung fehlgeschlagen. Rechner bleibt an.')
        if digest == PATCH_HASH and hashlib.sha256(MODULE.read_bytes()).hexdigest() != digest:
            raise RuntimeError('Teacher-Winbind-Patch hat sich geändert.')
        reboot = Path('/run/reboot-required').exists() or any(
            VERSIONED_KERNEL.fullmatch(package_name(name)) and package_name(name) != running
            and package_name(name) in bootable for name in upgrade['install'])
        result = dict(id=request['id'], status='success', boot_id=request['boot_id'],
                      reboot_required=reboot, time=datetime.now(BERLIN).isoformat())
        save('result', result)
        if reboot:
            subprocess.run(['systemctl', '--no-block', 'reboot'], check=True, timeout=15)


def run():
    require_teacher()
    with lock('worker'):
        request = load('request')
        if not request or not re.fullmatch('[0-9a-f]{32}', request.get('id', '')):
            raise ValueError('Teacher-Auftrag fehlt.')
        previous = load('result')
        if previous and previous.get('id') == request['id'] and previous.get('status') in ('success', 'failed'):
            return  # A duplicate unit start must preserve the completed result.
        try:
            if not previous or previous.get('id') != request['id'] or previous.get('status') != 'pending':
                raise RuntimeError('Teacher-Auftrag ist nicht als neuer Paketlauf bestätigt.')
            age = datetime.now(BERLIN) - datetime.fromisoformat(request['time'])
            if (request.get('boot_id') != boot_id()
                    or not timedelta(0) <= age <= timedelta(minutes=5)
                    or (not request.get('manual_pilot') and not start_allowed(datetime.now(BERLIN)))):
                raise RuntimeError('Teacher-Auftrag ist veraltet oder außerhalb des Startfensters.')
            save('result', dict(id=request['id'], status='running', boot_id=request['boot_id']))
            maintain(request)
        except BaseException as error:
            save('result', dict(id=request['id'], status='failed', boot_id=boot_id(),
                                time=datetime.now(BERLIN).isoformat(), detail=str(error)[:500]))
            raise


def software_dispatch(payload):
    """Allow package profiles, never remote code, shell arguments or playbooks."""
    require_teacher()
    import software_client
    import deb_client
    if not isinstance(payload, dict):
        raise ValueError('Ungültiger Teacher-Softwareauftrag.')
    operation = payload.get('operation')
    revision = hashlib.sha256(Path(software_client.__file__).read_bytes()+Path(deb_client.__file__).read_bytes()).hexdigest()
    if payload == {'operation': 'capabilities'}:
        import importlib.util
        if importlib.util.find_spec('apt') is None:
            raise ValueError('Für Teacher-Softwareprofile fehlt python3-apt.')
        return dict(host='LAB-Teacher', protocol=2, revision=revision)
    fields = {'operation', 'revision'}
    if operation in ('plan', 'install'):
        fields.add('packages')
    if operation == 'install':
        fields.add('expected')
    if operation=='cached' or 'debs' in payload:
        fields.add('debs')
    if operation not in ('inventory', 'cached', 'plan', 'install') or set(payload) != fields or payload['revision'] != revision:
        raise ValueError('Teacher-Softwarezugang zuerst mit dem aktuellen Installer aktualisieren.')
    if operation == 'inventory':
        result = read('dpkg-query', '-W', '-f=${binary:Package}\t${Architecture}\t${db:Status-Status}\t${Version}\n')
        if len(result) > 2097152:
            raise ValueError('Teacher-Paketstand ist zu groß.')
        return dict(output=result)
    debs=deb_client.descriptors(payload.get('debs',[]))
    if operation=='cached':return deb_client.cached(debs,'LAB-Teacher')
    names = software_client.packages(payload['packages'])
    if operation == 'install' and (not isinstance(payload['expected'], str)
                                   or not re.fullmatch('[a-f0-9]{64}', payload['expected'])):
        raise ValueError('Teacher-Installation benötigt eine gültige Paketvorschau.')
    # Reuse maintenance locks. Manual software may run with an active desktop;
    # no login gate, logout, reboot or poweroff belongs to this operation.
    with lock('endpoint'), lock('worker'):
        if active() not in ('inactive', 'failed') or not apt_idle() or read('dpkg', '--audit'):
            raise RuntimeError('Teacher-Paketwartung läuft oder dpkg benötigt eine Reparatur.')
        if debs:
            return (software_client.plan(names,'LAB-Teacher',debs) if operation=='plan' else
                    software_client.install(names,'LAB-Teacher',payload['expected'],debs))
        return (software_client.plan(names, 'LAB-Teacher') if operation == 'plan' else
                software_client.install(names, 'LAB-Teacher', payload['expected']))


def endpoint():
    if len(sys.argv) != 1:
        raise SystemExit('Teacher-Empfänger akzeptiert keine Befehlsargumente.')
    replied = False

    def reply(data):
        nonlocal replied
        print(json.dumps(data, ensure_ascii=False), flush=True)
        replied = True

    try:
        text=sys.stdin.buffer.readline(201).decode('utf-8')
        upload=re.fullmatch(r'software-upload ([a-f0-9]{64}) ([1-9][0-9]{0,9})\n',text)
        if upload:
            require_teacher()
            import deb_client
            with lock('endpoint'),lock('worker'):
                if active() not in ('inactive','failed'):raise RuntimeError('Teacher-Wartung läuft.')
                response=deb_client.receive(sys.stdin.buffer,'LAB-Teacher',upload[1],int(upload[2]))
        elif text=='software\n':
            body=sys.stdin.buffer.read(32769)
            if len(body)>32768:raise ValueError('Teacher-Auftrag zu lang.')
            response=software_dispatch(json.loads(body))
        else:
            response=dispatch(text,acknowledge=reply)
        if not replied:
            reply(response)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
