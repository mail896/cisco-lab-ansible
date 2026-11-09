"""Playbook catalogue, fresh-client queue, and maintenance pilot."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import shlex
import subprocess
from tempfile import TemporaryDirectory
import threading
import uuid

from dashboard.admission import (Bootstrap, VEYON_KEY, veyon_available,
                                 read_settings, validate_hardware)
from dashboard.deploy import ADMISSIONS, JOBS, LAB_TARGETS, ROOT, Deployments, close_control_socket, known_hosts_for
from dashboard.runtime import ansible_tool
from maintenance.preview import BERLIN
from maintenance.reservations import reserved

ENTRIES = {key:{'label':label,'source':entry,'kind':'pilot'} for key,(label,_,entry) in JOBS.items()}
ENTRIES.update(
    bootstrap={'label':'SSH-Erstaufnahme','source':'bootstrap-client.yml','kind':'local'},
    fresh={'label':'Vollständige Baseline für neues Image','source':'lab-baseline.yml','kind':'fresh'},
    winbind={'label':'Winbind-Reconnect-Fix','source':'winbind-reconnect.yml','kind':'local'},
    maintenance={'label':'APT-Wartungspilot','source':'maintenance/pilot.py','kind':'maintenance'},
)
FRESH_WORKERS = 4
IDLE_WARNING = 5 * 60
ACTIVE = {'queued', 'running'}


def packettracer_eula_version(root=ROOT):
    """No implicit approval in a public checkout; operator approval stays private."""
    try:
        version = (root/'local-private/packettracer-eula-version').read_text().strip()
    except FileNotFoundError:
        return ''
    if version != '9.0':
        raise ValueError('Betreiberzustimmung passt nicht zur festgelegten Packet-Tracer-Version 9.0.')
    return version


def packettracer_defaults(root=ROOT):
    """Private, neutral preferences generated without any Cisco user account."""
    path = root / 'local-private/packettracer-9.0.defaults'
    if not path.exists():
        return ''
    if path.is_symlink() or not path.is_file() or not 1 <= path.stat().st_size <= 65536:
        raise ValueError('Neutrale Packet-Tracer-Startvorgaben sind ungültig.')
    return str(path)


def source(entry):
    if entry not in ENTRIES:
        raise FileNotFoundError
    return (ROOT/ENTRIES[entry]['source']).read_text()


def ssh_command(host, known_hosts_b, command):
    known = known_hosts_for(host, known_hosts_b)
    if not known or not known.is_file():
        raise ValueError('Verifizierter SSH-Hostschlüssel fehlt.')
    return ['/usr/bin/ssh','-T','-F','/dev/null','-o','BatchMode=yes','-o','IdentitiesOnly=yes',
            '-o','StrictHostKeyChecking=yes','-o','GlobalKnownHostsFile=/dev/null',
            '-o',f'UserKnownHostsFile={known}','-o','ConnectTimeout=5',
            '-i',str(Path.home()/'.ssh/id_ed25519'),
            f'administrator@{LAB_TARGETS[host]}',shlex.join(command)]


class Provision:
    def __init__(self, directory, deployments, controller_profile='teacher'):
        self.directory = directory
        self.deployments = deployments
        directory.mkdir(mode=0o700,parents=True,exist_ok=True)
        self.state_lock = threading.RLock()
        self.bootstrap = Bootstrap(directory, deployments.lock, controller_profile)
        for item in self.records():
            if item['status'] in ACTIVE:
                item.update(status='interrupted', finished=self.now())
                self.save(item)
        from dashboard.intake import Intake
        self.intake = Intake(self) if controller_profile == 'apps' else None

    @staticmethod
    def now():
        return datetime.now(BERLIN).isoformat(timespec='microseconds')

    def save(self, run):
        path = self.directory/(run['id']+'.json')
        tmp = path.with_suffix('.tmp')
        tmp.write_text(json.dumps(run, ensure_ascii=False))
        tmp.replace(path)

    def records(self):
        return sorted((json.loads(path.read_text()) for path in self.directory.glob('*.json')),
                      key=lambda run: (run['started'], run['id']), reverse=True)

    def record(self, run_id):
        Deployments.validate_id(run_id)
        return json.loads((self.directory/(run_id+'.json')).read_text())

    @staticmethod
    def admission(host, record_id=None):
        if host not in LAB_TARGETS:
            raise ValueError('Bitte einen LAB-Client wählen.')
        path = ADMISSIONS/(host+'.json' if record_id is None else host+'.'+record_id+'.used')
        data = read_settings(path)
        validate_hardware(host, data)
        known_value = data.get('lab_fresh_known_hosts')
        if not isinstance(known_value, str):
            raise ValueError('Verifizierte SSH-Hostdatei fehlt.')
        known = Path(known_value).expanduser()
        if not known.is_absolute() or not known.is_file():
            raise ValueError('Verifizierte SSH-Hostdatei fehlt.')
        return path, known

    @staticmethod
    def reserve_maintenance(host):
        if reserved(host):
            raise ValueError(host + ' ist für die Wartung reserviert.')

    def fresh_state(self, limit=30):
        ready = []
        with self.state_lock:
            for host in LAB_TARGETS:
                try:
                    self.admission(host)
                    self.reserve_maintenance(host)
                except (OSError, ValueError):
                    continue
                ready.append(host)
            records = self.records()
            retries = set()
            for run in records:
                host = run['host']
                if run.get('kind') == 'bootstrap' or host in retries:
                    continue
                retries.add(host)
                if (run['status'] in {'failed', 'interrupted', 'stopped'}
                        and run.get('pin_sha256') and host not in ready
                        and not (ADMISSIONS/(host+'.json')).exists()):
                    try:
                        _, known = self.admission(host, run['admission_id'])
                        self.reserve_maintenance(host)
                        run['retryable'] = hashlib.sha256(known.read_bytes()).hexdigest() == run['pin_sha256']
                    except (OSError, ValueError):
                        pass
            active = [run for run in records if run['status'] in ACTIVE]
            for run in active:
                if run['status'] != 'running' or run.get('kind') == 'bootstrap':
                    continue
                path = self.directory/(run['id']+'.log')
                try:
                    run['quiet'] = datetime.now().timestamp() - path.stat().st_mtime >= IDLE_WARNING
                    run['task'] = next((line for line in reversed(self.output(run['id']).splitlines())
                                        if line.startswith('TASK [')), '').split(' ***', 1)[0]
                except FileNotFoundError:
                    pass
            recent = [run for run in records if run['status'] not in ACTIVE][:limit]
            return dict(hosts=ready, hardware=self.bootstrap.targets(), runs=active + recent,
                        veyon=veyon_available(), parallelism=FRESH_WORKERS,
                        busy=self.deployments.lock.locked())

    @staticmethod
    def validate_fresh_inputs():
        private = ROOT/'local-private/cisco-account-vars.json'
        if private.is_symlink() or not private.is_file() or private.stat().st_mode & 0o077:
            raise ValueError('Die private Vorgabe für das lokale Sammelkonto fehlt oder ist nicht geschützt.')
        candidate = ROOT/'local-private/winbind/ad.so'
        if (candidate.is_symlink() or not candidate.is_file()
                or hashlib.sha256(candidate.read_bytes()).hexdigest()
                != '61fe179121bcc6d3442d1ded9b59e9b5239daae4adc905ac6e487735d81b89e9'):
            raise ValueError('Das geprüfte Winbind-Modul fehlt. Keine Basisinstallation gestartet.')

    def fresh_start(self, targets, confirmed, group_id=''):
        from dashboard.software import baseline_packages
        from dashboard.own_packages import OwnPackages
        if not isinstance(group_id, str):
            raise ValueError('Ungültige Playbook-Gruppe.')
        if confirmed is not True:
            raise ValueError('Basisinstallation ausdrücklich bestätigen.')
        if not isinstance(targets, list) or not 1 <= len(targets) <= len(LAB_TARGETS):
            raise ValueError('Mindestens einen vorbereiteten Client wählen.')
        selected = {}
        for item in targets:
            if (not isinstance(item, dict) or set(item) != {'host', 'veyon'}
                    or not isinstance(item['host'], str) or item['host'] not in LAB_TARGETS
                    or type(item['veyon']) is not bool or item['host'] in selected):
                raise ValueError('Jedes Ziel genau einmal mit Veyon-Auswahl angeben.')
            selected[item['host']] = item['veyon']
        self.check_intake_pilot(selected)
        if any(selected.values()) and not veyon_available():
            raise ValueError('Der geprüfte öffentliche Veyon-Lehrerschlüssel fehlt.')
        self.validate_fresh_inputs()
        if not self.deployments.lock.acquire(blocking=False):
            return None
        try:
            # Package uploads hold the same lock: snapshot only after acquiring
            # it, so an unreviewed replacement cannot enter the fresh baseline.
            software = baseline_packages()
            group = self.playbook_groups.fresh_selection(group_id) if group_id else None
            if group:
                from dashboard.software_client import packages
                additional = {name for step in group['steps'] for name in step.get('packages', [])}
                if additional:
                    software = packages(sorted(set(software) | additional))
            with self.state_lock:
                runs = []
                for host, veyon in selected.items():
                    self.reserve_maintenance(host)
                    _, known = self.admission(host)
                    run_id = uuid.uuid4().hex
                    runs.append(dict(id=run_id, host=host, veyon=veyon, admission_id=run_id,
                                     software_packages=software,
                                     software_debs=OwnPackages().snapshot(software),
                                     pin_sha256=hashlib.sha256(known.read_bytes()).hexdigest(),
                                     phase='basis', started=self.now(), status='queued'))
                    if group:
                        runs[-1]['playbook_group'] = group
                return self.queue(runs)
        except BaseException:
            self.deployments.lock.release()
            raise

    def retry(self, run_id, confirmed):
        if confirmed is not True:
            raise ValueError('Fortsetzung ausdrücklich bestätigen.')
        self.deployments.validate_id(run_id)
        if not self.deployments.lock.acquire(blocking=False):
            return None
        try:
            with self.state_lock:
                old = next((run for run in self.records() if run['id'] == run_id), None)
                if not old or not old.get('pin_sha256'):
                    raise ValueError('Für diesen Lauf ist kein GUI-Wiederanlauf hinterlegt.')
                host = old['host']
                self.check_intake_pilot([host])
                self.reserve_maintenance(host)
                if not any(run['id'] == run_id and run.get('retryable')
                           for run in self.fresh_state()['runs']):
                    raise ValueError('Der Lauf ist nicht mehr aktuell oder die Hostprüfung fehlt.')
                phase = old['phase']
                if phase == 'basis' and old['veyon'] and not veyon_available():
                    raise ValueError('Der geprüfte öffentliche Veyon-Lehrerschlüssel fehlt.')
                run = {key: old[key] for key in ('host', 'veyon', 'phase', 'admission_id', 'pin_sha256')}
                run['software_packages'] = old.get('software_packages', [])
                run['software_debs']=old.get('software_debs',[])
                if old.get('playbook_group'):
                    run['playbook_group'] = old['playbook_group']
                run.update(id=uuid.uuid4().hex, started=self.now(), status='queued', resumed_from=run_id)
                return self.queue([run])
        except BaseException:
            self.deployments.lock.release()
            raise

    def check_intake_pilot(self, hosts):
        if self.intake and not self.intake.pilot_accepted() and set(hosts) != {'LAB-08B'}:
            raise ValueError('Zuerst LAB-08B über die GUI installieren und praktisch abnehmen.')

    def queue(self, runs):
        for run in runs:
            self.save(run)
        threading.Thread(target=self.install_batch, args=(runs,), daemon=True).start()
        return {'runs': [dict(run) for run in runs]}

    def bootstrap_start(self, host, fingerprint, ssh_password, sudo_password, confirmed):
        if not isinstance(host, str) or host not in LAB_TARGETS:
            raise ValueError('Bitte einen LAB-Client wählen.')
        return self.bootstrap.start(host, fingerprint, ssh_password, sudo_password, confirmed,
                                    self.save, self.now)

    def install_batch(self, runs):
        try:
            with ThreadPoolExecutor(max_workers=FRESH_WORKERS) as workers:
                list(workers.map(self.install, runs))
        finally:
            self.deployments.lock.release()

    def stop(self, run_id, confirmed):
        if confirmed is not True:
            raise ValueError('Anhalten ausdrücklich bestätigen.')
        self.deployments.validate_id(run_id)
        with self.state_lock:
            path = self.directory/(run_id+'.json')
            run = json.loads(path.read_text())
            if run['status'] not in ACTIVE or 'phase' not in run or (
                    run['status'] == 'running' and run['phase'] != 'basis'):
                raise ValueError('Dieser Lauf kann nicht mehr angehalten werden.')
            run['stop_requested'] = True
            self.save(run)
            return {'stop_requested': True}

    def should_stop(self, run):
        current = json.loads((self.directory/(run['id']+'.json')).read_text())
        return current.get('stop_requested', False)

    def install(self, run):
        host = run['host']
        try:
            with (self.directory/(run['id']+'.log')).open('w', buffering=1) as log:
                try:
                    with self.state_lock:
                        if self.should_stop(run):
                            log.write('Vor Beginn angehalten.\n')
                            run['status'] = 'stopped'
                            return
                        self.reserve_maintenance(host)
                        record_id = run['admission_id']
                        if 'resumed_from' in run:
                            admission, known = self.admission(host, record_id)
                        else:
                            pending, known = self.admission(host)
                            admission = pending.with_suffix('.'+record_id+'.used')
                        if hashlib.sha256(known.read_bytes()).hexdigest() != run['pin_sha256']:
                            raise ValueError('SSH-Hostdatei wurde seit der Freigabe geändert.')
                        if 'resumed_from' not in run:
                            pending.replace(admission)
                        run['status'] = 'running'
                        self.save(run)
                    self.install_phases(run, admission, known, log)
                except (OSError, ValueError, subprocess.SubprocessError) as error:
                    log.write(f'\nInstallation unterbrochen: {error}\n')
                    run['status'] = 'failed'
        finally:
            with self.state_lock:
                run.update(finished=self.now())
                if run['status'] in ACTIVE:
                    run['status'] = 'failed'
                self.save(run)

    def install_phases(self, run, admission, known, log):
        host = run['host']
        if run.get('playbook_group'):
            log.write('Playbook-Gruppe: '+run['playbook_group']['name']+' · gemeinsame Bausteine sind bereits in der Grundkonfiguration enthalten.\n')
        secure = ('-F /dev/null -o BatchMode=yes -o IdentitiesOnly=yes '
                  '-o StrictHostKeyChecking=yes -o ConnectTimeout=8 '
                  '-o ServerAliveInterval=15 -o ServerAliveCountMax=3 '
                  f'-o UserKnownHostsFile={shlex.quote(str(known))} -o GlobalKnownHostsFile=/dev/null')
        images = self.deployments.appearance.active()
        with TemporaryDirectory(prefix='lab-fresh-ssh-') as directory:
            control_socket = Path(directory)/host
            settings = dict(lab_fresh_install_veyon=run['veyon'], lab_fresh_veyon_public_key=str(VEYON_KEY),
                            lab_fresh_packettracer_eula_version=packettracer_eula_version(),
                            lab_fresh_packettracer_defaults=packettracer_defaults(),
                            lab_software_packages=run.get('software_packages', []),
                            lab_software_debs=run.get('software_debs',[]),
                            lab_software_deb_directory=str(Path.home()/'.local/state/cisco-lab-dashboard/software/packages'),
                            ansible_host=LAB_TARGETS[host], ansible_user='administrator',
                            ansible_ssh_common_args=secure,
                            ansible_ssh_args='-o ControlMaster=auto -o ControlPersist=30s',
                            ansible_control_path=str(control_socket), ansible_control_path_dir=directory,
                            lab_greeter_image_source=str(images['lock'][1]),
                            lab_desktop_image_source=str(images['desktop'][1]))
            try:
                if run['phase'] == 'basis':
                    log.write('Phase 1/3 · LAB-Basis einschließlich Winbind\n')
                    command = [ansible_tool('ansible-playbook'), '-i', 'inventory.ini', '--limit', host,
                               '--private-key', str(Path.home()/'.ssh/id_ed25519'),
                               '-e', '@'+str(admission), '-e', json.dumps(settings), 'fresh-client.yml']
                    if self.run_playbook(command, log):
                        run['status'] = 'failed'
                        return
                    with self.state_lock:
                        stop = self.should_stop(run)
                        run['phase'] = 'cisco'
                        if stop:
                            run['status'] = 'stopped'
                            log.write('Nach erfolgreicher Basis angehalten. Das Sammelkonto ist noch offen.\n')
                        self.save(run)
                    if stop:
                        return
                if run['phase'] == 'cisco':
                    log.write('Phase 2/3 · Lokales Sammelkonto\n')
                    cisco = self.deployments.command(host, 'cisco', release=True,
                                                     control_socket=control_socket, known_hosts=known)
                    cisco[-2] = json.dumps(dict(json.loads(cisco[-2]), ansible_ssh_common_args=secure))
                    if self.run_playbook(cisco, log):
                        run['status'] = 'failed'
                        return
                    with self.state_lock:
                        run['phase'] = 'maintenance'
                        self.save(run)
                log.write('Phase 3/3 · Abendwartung und zentrale Registrierung\n')
                maintenance_settings = dict(settings, lab_maintenance_host=host,
                                            lab_maintenance_known_hosts=str(known))
                command = [ansible_tool('ansible-playbook'), '-i', 'inventory.ini', '--limit', host,
                           '--private-key', str(Path.home()/'.ssh/id_ed25519'),
                           '-e', '@'+str(admission), '-e', json.dumps(maintenance_settings), 'maintenance-client.yml']
                run['status'] = 'failed' if self.run_playbook(command, log) else 'succeeded'
                if run['status'] == 'succeeded':
                    log.write('Installation einschließlich Winbind, Sammelkonto und Abendwartung abgeschlossen.\n')
            finally:
                close_control_socket(control_socket, host)

    @staticmethod
    def run_playbook(command, log):
        result = subprocess.run(command, cwd=ROOT, stdin=subprocess.DEVNULL,
                                stdout=log, stderr=subprocess.STDOUT)
        return result.returncode

    def output(self, run_id):
        self.deployments.validate_id(run_id)
        path = self.directory/(run_id+'.log')
        with path.open('rb') as file:
            file.seek(max(0, path.stat().st_size-65536))
            return file.read().decode(errors='replace')

    @staticmethod
    def maintenance_hosts():
        from maintenance.controller import targets
        return sorted(targets())

    def maintenance_state(self, host='LAB-08A'):
        from maintenance.controller import ssh, targets
        enrolled = targets()
        if host not in enrolled:
            raise ValueError('Dieser Client ist noch nicht für die zentrale Wartung registriert.')
        script=('systemctl show -p LoadState --value cisco-lab-maintenance.service; '
                'printf "%s\\n" "$(systemctl show -p ActiveState --value cisco-lab-maintenance.service)"; '
                'sudo -n journalctl -u cisco-lab-maintenance.service -n 30 --no-pager')
        result=subprocess.run(ssh(host,enrolled[host],['sh','-c',script]),
                              text=True,capture_output=True,timeout=15)
        if result.returncode:
            return {'available':False,'can_start':False,'output':host + ' oder Wartungsprotokoll nicht erreichbar.'}
        lines=result.stdout.splitlines()
        loaded = bool(lines) and lines[0] == 'loaded'
        active = lines[1] if len(lines) > 1 else 'unknown'
        return {'host':host, 'available':loaded, 'active':active,
                'can_start':loaded and active in ('inactive','failed')
                            and not self.deployments.lock.locked(),
                'output':'\n'.join(lines[2:])}

    def maintenance_start(self, confirmed, host='LAB-08A', hosts=None):
        from maintenance.controller import execute, targets
        enrolled = targets()
        selected = [host] if hosts is None else hosts
        if (not isinstance(selected, list) or not 1 <= len(selected) <= len(LAB_TARGETS)
                or any(not isinstance(name, str) for name in selected)
                or len(set(selected)) != len(selected)):
            raise ValueError('Bitte einen oder mehrere unterschiedliche Schülerclients auswählen.')
        if any(name not in enrolled for name in selected):
            raise ValueError('Dieser Client ist noch nicht für die zentrale Wartung registriert.')
        if confirmed is not True:
            raise ValueError('Abmeldung, Updates und Neustart ausdrücklich bestätigen.')
        if not self.deployments.lock.acquire(blocking=False):
            return None
        try:
            threading.Thread(target=execute, args=({name: enrolled[name] for name in sorted(selected)}, True, self.deployments.lock),
                             daemon=True).start()
            return {'started':True}
        except BaseException:
            self.deployments.lock.release()
            raise
