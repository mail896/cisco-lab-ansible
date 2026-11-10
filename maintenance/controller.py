"""Wake and maintain explicitly enrolled LAB clients from the selected controller."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shlex
import socket
import subprocess
import tempfile
import time
import uuid

from dashboard.admission import read_settings, write_private
from dashboard.deploy import LAB_TARGETS, ROOT
from dashboard.operation_lock import OperationLock
from dashboard.room import RoomActions
from maintenance.preview import BERLIN, CLIENT_WORKERS, start_allowed

CONFIG = ROOT/'local-private/maintenance-targets.json'
PIN_DIRECTORY = ROOT/'local-private/maintenance-pins'
STATE = Path.home()/'.local/state/cisco-lab-dashboard/maintenance'

PROBE = r'''
import json, re, socket, subprocess, sys
from pathlib import Path
host = sys.argv[1]
if socket.gethostname() != host:
    raise SystemExit('Falsche Geräteidentität.')
def read(*args):
    return subprocess.check_output(args, text=True, timeout=15).strip()
scopes = re.search(r'session-([^.]+)\.scope', Path('/proc/self/cgroup').read_text())
own = scopes.group(1) if scopes else None
occupied = False
for row in read('loginctl', 'list-sessions', '--no-legend').splitlines():
    session = row.split()[0]
    if session == own:
        continue
    kind = read('loginctl', 'show-session', session, '-p', 'Class', '--value')
    if not kind:
        raise SystemExit('Sitzung nicht eindeutig.')
    occupied |= kind.startswith('user')
result = Path('/var/lib/cisco-lab-maintenance/result.json')
manifest = json.loads(Path('/etc/cisco-lab/network.json').read_text())
command = subprocess.run(['systemctl','show','cisco-lab-maintenance.service','-p','ExecStart','--value'], text=True, capture_output=True)
print(json.dumps(dict(host=host, uptime=float(Path('/proc/uptime').read_text().split()[0]),
  boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
  school_mac=manifest['identity']['school']['mac'].lower(),
  evening='--evening-restart' in command.stdout.split(),
  active=read('systemctl','show','cisco-lab-maintenance.service','-p','ActiveState','--value'),
  occupied=occupied, home_mounted=any(line.startswith('/var/lib/iserv/client/home/')
      for line in read('findmnt','-n','-o','TARGET').splitlines()),
  result=json.loads(result.read_text()) if result.is_file() else None)))
'''


def now():
    return datetime.now(BERLIN).isoformat(timespec='seconds')


def targets():
    if not CONFIG.exists():
        return {}
    data=read_settings(CONFIG)
    if data.get('version') != 1 or not isinstance(data.get('targets'),dict):
        raise ValueError('Ungültige Wartungsregistrierung.')
    macs=RoomActions.macs()
    for host, record in data['targets'].items():
        if host not in LAB_TARGETS or record.get('mac') != macs[host]:
            raise ValueError('Wartungsziel und geprüftes WOL-Inventar stimmen nicht überein.')
        pin=Path(record.get('known_hosts',''))
        if (not pin.is_absolute() or pin.is_symlink() or not pin.is_file()
                or pin.stat().st_mode & 0o077
                or hashlib.sha256(pin.read_bytes()).hexdigest()!=record.get('pin_sha256')):
            raise ValueError('Verifizierte SSH-Datei des Wartungsziels wurde geändert.')
    return data['targets']


def register(host, known, school_mac):
    if host not in LAB_TARGETS:
        raise ValueError('Unbekannter LAB-Client.')
    macs=RoomActions.macs()
    if school_mac.lower()!=macs[host]:
        raise ValueError('Schul-MAC und WOL-Inventar widersprechen sich.')
    known=known.expanduser().resolve()
    if not known.is_file() or known.stat().st_mode & 0o077:
        raise ValueError('Private verifizierte Hostdatei fehlt.')
    result=subprocess.run(['ssh-keygen','-F',LAB_TARGETS[host],'-f',str(known)],capture_output=True,text=True,check=True)
    keys='\n'.join(line for line in result.stdout.splitlines() if line and not line.startswith('#'))+'\n'
    if not keys.strip():
        raise ValueError('Verifizierter Schlüssel für diesen Client fehlt.')
    CONFIG.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    PIN_DIRECTORY.mkdir(mode=0o700,exist_ok=True)
    with (CONFIG.parent/'maintenance-registration.lock').open('a+b') as lock:
        os.fchmod(lock.fileno(),0o600)
        fcntl.flock(lock,fcntl.LOCK_EX)
        enrolled=targets()
        checksum=hashlib.sha256(keys.encode()).hexdigest()
        pin=PIN_DIRECTORY/(host+'-'+checksum+'.known_hosts')
        if pin.exists() or pin.is_symlink():
            if (pin.is_symlink() or not pin.is_file() or pin.stat().st_mode & 0o077
                    or pin.stat().st_uid != os.getuid() or pin.read_text() != keys):
                raise ValueError('Vorhandene SSH-Pindatei entspricht nicht dem geprüften Schlüssel.')
        else:
            write_private(pin,keys)
        enrolled[host]=dict(known_hosts=str(pin),mac=macs[host],pin_sha256=checksum)
        data=dict(version=1,targets=enrolled)
        with tempfile.NamedTemporaryFile(mode='w', prefix='.maintenance-targets-',
                                         dir=CONFIG.parent, delete=False) as output:
            temporary=Path(output.name)
            try:
                json.dump(data,output,ensure_ascii=False,indent=2)
                output.write('\n')
                output.flush()
                os.fsync(output.fileno())
                temporary.replace(CONFIG)
            finally:
                temporary.unlink(missing_ok=True)
    print(host+' für zentrale Wartung registriert.')


def ssh(host, record, args):
    return ['/usr/bin/ssh','-T','-F','/dev/null','-o','BatchMode=yes','-o','IdentitiesOnly=yes',
            '-o','StrictHostKeyChecking=yes','-o','GlobalKnownHostsFile=/dev/null',
            '-o','UserKnownHostsFile='+record['known_hosts'],'-o','ConnectTimeout=4',
            '-o','ControlMaster=no','-o','ControlPath=none',
            '-i',str(Path.home()/'.ssh/id_ed25519'),'administrator@'+LAB_TARGETS[host],shlex.join(args)]


def probe(host, record):
    result=subprocess.run(ssh(host,record,['sudo','-n','python3','-IB','-c',PROBE,host]),
                          text=True,capture_output=True,timeout=35)
    if result.returncode:
        if 'Kurze LAB-Wartung' in result.stderr:
            return None
        if any(message in result.stderr for message in ('Host key verification failed', 'IDENTIFICATION HAS CHANGED',
                                                        'Permission denied', 'a password is required')):
            raise RuntimeError('SSH-Schlüssel oder Verwaltungsrechte des Clients müssen geprüft werden.')
        return None
    data=json.loads(result.stdout)
    if data.get('host')!=host or data.get('school_mac')!=record['mac']:
        raise ValueError('Geräteidentität oder Schul-MAC widerspricht der Registrierung.')
    return data


def reachable(host):
    try:
        with socket.create_connection((LAB_TARGETS[host],22),timeout=2):
            return True
    except OSError:
        return False


def start_service(host, record, manual):
    if manual:
        code=r'''from pathlib import Path
import socket,subprocess,sys
assert socket.gethostname()==sys.argv[1]
assert subprocess.check_output(['systemctl','show','cisco-lab-maintenance.service','-p','ActiveState','--value'],text=True).strip() in ('inactive','failed')
directory=Path('/run/systemd/system/cisco-lab-maintenance.service.d')
directory.mkdir(parents=True,exist_ok=True)
path=directory/'50-controller-pilot.conf'
with path.open('x') as file:
 file.write('[Service]\nExecStart=\nExecStart=/usr/bin/python3 -B -m maintenance.pilot --host '+sys.argv[1]+' --evening-restart --manual-pilot\n')
subprocess.run(['systemctl','daemon-reload'],check=True)
subprocess.run(['systemctl','start','--no-block','cisco-lab-maintenance.service'],check=True)
'''
        command=['sudo','-n','python3','-IB','-c',code,host]
    else:
        command=['sudo','-n','systemctl','start','--no-block','cisco-lab-maintenance.service']
    subprocess.run(ssh(host,record,command),capture_output=True,text=True,check=True,timeout=20)


def new_boot_after_wake(uptime, elapsed):
    return 0 <= uptime <= elapsed + 3


def history():
    records = [json.loads(path.read_text()) for path in STATE.glob('*.json')]
    return sorted(records, key=lambda record: record['started'], reverse=True)


def completed_today():
    today = datetime.now(BERLIN).date()
    completed = set()
    for record in history():
        if record.get('manual_pilot') or datetime.fromisoformat(record['started']).date() != today:
            continue
        completed.update(host for host, result in record.get('results', {}).items()
                         if result.get('status') == 'succeeded')
    return completed


def on_host(host, record, route, manual):
    was_reachable=reachable(host)
    wake_started=time.monotonic()
    if not was_reachable:
        RoomActions.wake(record['mac'],route)
        print(host + ': WOL gesendet; Erreichbarkeit wird geprüft.', flush=True)
    deadline=time.monotonic()+900
    first=None
    while time.monotonic()<deadline:
        first=probe(host,record)
        if first is not None:
            break
        time.sleep(10)
    if first is None:
        raise RuntimeError('Client nach Aufwecken oder laufender Wartung nicht erreichbar.')
    woke=not was_reachable and new_boot_after_wake(first['uptime'],time.monotonic()-wake_started)
    print(host + ': Identität geprüft; Wartungszustand wird verfolgt.', flush=True)
    if not first['evening']:
        return dict(status='skipped',reason='Client hat noch nicht den neuen Abendmodus.',woke=False)
    previous=first.get('result') or {}
    already_done=(previous.get('status')=='success' and previous.get('boot_id')!=first['boot_id']
                  and datetime.fromisoformat(previous['time']).astimezone(BERLIN).date()==datetime.now(BERLIN).date())
    requested=datetime.now(BERLIN)-timedelta(seconds=1)
    if first['active'] in ('inactive','failed') and (manual or not already_done):
        if not manual and not start_allowed(datetime.now(BERLIN)):
            return dict(status='deferred', reason='Startfenster beendet; kein neuer Paketlauf.', woke=woke)
        start_service(host,record,manual)
    final=first
    last_reachable=time.monotonic()
    inactive_since = None
    while True:
        data=probe(host,record)
        if data is not None:
            last_reachable=time.monotonic()
            result=data.get('result') or {}
            fresh=result.get('time') and datetime.fromisoformat(result['time'])>=requested
            if result.get('status')=='failed' and fresh:
                detail = str(result.get('detail') or '').strip()[:500]
                raise RuntimeError('Clientwartung fehlgeschlagen'+(': '+detail if detail else '.')+
                                   ' Client bleibt eingeschaltet.')
            completed_before_start = already_done and not manual and data['active'] == 'inactive'
            current_boot_was_maintained = result.get('boot_id') == first['boot_id']
            if (result.get('status')=='success' and result.get('boot_id')!=data['boot_id']
                    and ((fresh and current_boot_was_maintained) or completed_before_start)):
                final=data
                break
            if data['active'] == 'failed':
                raise RuntimeError('Wartungsdienst fehlgeschlagen. Client bleibt eingeschaltet.')
            if data['active'] == 'inactive':
                inactive_since = inactive_since or time.monotonic()
                if time.monotonic() - inactive_since > 120:
                    raise RuntimeError('Dienst beendet, aber kein erfolgreicher Neustart bestätigt.')
            else:
                inactive_since = None
        elif reachable(host):
            # SSH can be blocked by the maintenance login gate for a long installation.
            last_reachable=time.monotonic()
        elif time.monotonic()-last_reachable>300:
            raise RuntimeError('Client nach Wartungsstart über fünf Minuten nicht mehr erreichbar.')
        time.sleep(10)
    outcome=dict(status='succeeded',woke=woke,boot_id=final['boot_id'],result=final.get('result'),
                 initial_uptime=first['uptime'], initial_reachable=was_reachable)
    if woke and not final['occupied'] and not final['home_mounted']:
        class ManagedTargets:
            known_hosts_b=None
            def known_hosts_for(self, name):
                if name != host:
                    raise ValueError('Falsches Ausschaltziel.')
                return Path(record['known_hosts'])
        actions=RoomActions(STATE/'power-actions',ManagedTargets())
        outcome['poweroff']=actions.on_host(host,'poweroff',None,maintenance_owner=True)
        if outcome['poweroff'].startswith('Fehler:'):
            raise RuntimeError(outcome['poweroff'])
    return outcome


def cleanup_manual(host, record):
    """Remove only our exact transient pilot override after a failed trial."""
    code = r'''from pathlib import Path
import socket, subprocess, sys
assert socket.gethostname() == sys.argv[1]
active = subprocess.check_output(['systemctl', 'show', 'cisco-lab-maintenance.service', '-p', 'ActiveState', '--value'], text=True).strip()
if active not in ('inactive', 'failed'):
    raise SystemExit('Laufende Wartung bleibt unverändert.')
path = Path('/run/systemd/system/cisco-lab-maintenance.service.d/50-controller-pilot.conf')
expected = '[Service]\nExecStart=\nExecStart=/usr/bin/python3 -B -m maintenance.pilot --host ' + sys.argv[1] + ' --evening-restart --manual-pilot\n'
if path.exists():
    if path.is_symlink() or path.read_text() != expected:
        raise SystemExit('Unbekannte Pilotvorgabe bleibt unverändert.')
    path.unlink()
    subprocess.run(['systemctl', 'daemon-reload'], check=True)
'''
    subprocess.run(ssh(host, record, ['sudo', '-n', 'python3', '-IB', '-c', code, host]),
                   capture_output=True, text=True, check=True, timeout=20)


def run(manual=False, selected=None, controller_host='LAB-Teacher'):
    if controller_host not in ('LAB-Teacher', 'apps') or socket.gethostname().split('.')[0] != controller_host:
        raise RuntimeError('Zentrale Wartung nur auf dem ausdrücklich gewählten Controllerhost.')
    if not manual and not start_allowed(datetime.now(BERLIN)):
        print('Außerhalb des Wartungsfensters. Kein Start.')
        return
    enrolled=targets()
    if selected:
        if any(host not in enrolled for host in selected):
            raise ValueError('Client ist nicht für die zentrale Wartung registriert.')
        enrolled={host:enrolled[host] for host in selected}
    if not enrolled:
        raise ValueError('Keine Clients für den neuen Wartungsablauf registriert.')
    if not manual:
        done = completed_today()
        enrolled = {host: data for host, data in enrolled.items() if host not in done}
        if not enrolled:
            from maintenance import teacher_controller
            if (controller_host != 'apps' or not teacher_controller.enabled()
                    or teacher_controller.today('succeeded') or teacher_controller.today('failed')):
                print('Alle registrierten Clients sind heute bereits fertig. Kein erneutes Aufwecken.')
                return
    lock=OperationLock(STATE.parent/'operation.lock')
    if not lock.acquire(blocking=False):
        raise RuntimeError('Ein Verwaltungsauftrag läuft. Zentrale Wartung wurde zurückgestellt.')
    return execute(enrolled, manual, lock)


def execute(enrolled, manual, lock):
    """Track the complete run while owning the same lock as all GUI jobs."""
    try:
        return execute_locked(enrolled, manual)
    finally:
        lock.release()


def teacher_followup(record, manual):
    """Only the full daily client barrier may unlock the optional Teacher phase."""
    if manual or socket.gethostname().split('.')[0] != 'apps':
        return None
    from maintenance import teacher_controller
    if not teacher_controller.enabled():
        return {'status': 'disabled', 'reason': 'Teacher-Pilot und automatische Freigabe stehen aus.'}
    enrolled = set(targets())
    successful = completed_today() | {host for host, result in record['results'].items()
                                     if result.get('status') == 'succeeded'}
    if not enrolled or enrolled - successful or record['status'] != 'finished':
        return {'status': 'blocked', 'reason': 'Noch kein erfolgreicher Tagesabschluss aller registrierten Clients.'}
    return teacher_controller.run()


def invalidate_client_report(record):
    """Request one shared report after client work, excluding empty Teacher retries."""
    if not any(result.get('status') in ('succeeded', 'failed')
               for result in record.get('results', {}).values()):
        return
    path = STATE.parent / 'client-report-revision'
    with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, delete=False) as output:
        temporary = Path(output.name)
        try:
            output.write(record['id'] + '\n')
            output.flush()
            os.fchmod(output.fileno(), 0o600)
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def execute_locked(enrolled, manual):
    STATE.mkdir(mode=0o700,parents=True,exist_ok=True)
    record=dict(id=uuid.uuid4().hex,started=now(),status='running',results={},
                targets=sorted(enrolled),manual_pilot=manual,manual_start=manual)
    path=STATE/(record['id']+'.json')
    def save():
        temporary = path.with_suffix('.next')
        temporary.write_text(json.dumps(record, ensure_ascii=False, indent=2))
        temporary.chmod(0o600)
        temporary.replace(path)
    try:
        save()
        route=RoomActions.wake_route()
        with ThreadPoolExecutor(max_workers=CLIENT_WORKERS) as workers:
            jobs={workers.submit(on_host,host,data,route,manual):host for host,data in enrolled.items()}
            for future in as_completed(jobs):
                host=jobs[future]
                try:
                    record['results'][host] = future.result()
                except (OSError,ValueError,RuntimeError,subprocess.SubprocessError) as error:
                    record['results'][host]=dict(status='failed',reason=str(error))
                    if manual:
                        try:
                            cleanup_manual(host, enrolled[host])
                        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as cleanup_error:
                            record['results'][host]['cleanup_error'] = str(cleanup_error)
                save()
        record['status']='failed' if any(result['status']=='failed' for result in record['results'].values()) else 'finished'
        try:
            invalidate_client_report(record)
        except OSError as error:
            record['report_refresh_error'] = str(error)
        try:
            teacher = teacher_followup(record, manual)
            if teacher:
                record['teacher'] = teacher
                if teacher.get('status') == 'failed':
                    record['status'] = 'failed'
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
            record.update(status='failed', teacher={'status': 'failed', 'reason': str(error)})
    finally:
        record['finished']=now()
        if record['status'] == 'running':
            record['status'] = 'interrupted'
        save()
    print(json.dumps(record,ensure_ascii=False))
    if record['status'] == 'failed':
        raise SystemExit(1)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--register',choices=tuple(LAB_TARGETS))
    parser.add_argument('--known-hosts',type=Path)
    parser.add_argument('--school-mac')
    parser.add_argument('--manual-pilot',action='store_true')
    parser.add_argument('--hosts',nargs='+',choices=tuple(LAB_TARGETS))
    parser.add_argument('--controller-host',choices=('LAB-Teacher','apps'),default='LAB-Teacher')
    args=parser.parse_args()
    if args.register:
        if not args.known_hosts or not args.school_mac:
            parser.error('Verifizierte Hostdatei und Schul-MAC fehlen.')
        register(args.register,args.known_hosts,args.school_mac)
    else:
        run(args.manual_pilot, args.hosts, args.controller_host)


if __name__ == '__main__':
    main()
