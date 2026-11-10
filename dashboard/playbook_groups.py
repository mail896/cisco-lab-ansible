"""Named compositions of registered LAB playbooks and reviewed software profiles."""
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import threading
import uuid

from dashboard.deploy import JOBS, LAB_TARGETS, ROOT, close_control_socket
from dashboard.software import Software, package_revision
from dashboard.software_client import packages
from maintenance.reservations import reserved
from maintenance.preview import CLIENT_WORKERS


# Each module points to the existing command builder; no copied YAML or browser
# supplied file paths. These modules are already part of the mandatory baseline.
MODULES = {key: dict(id=key, label=value[0], source=value[2], in_baseline=True)
           for key, value in JOBS.items()}


class PlaybookGroups:
    def __init__(self, directory, deployments, software):
        self.directory, self.deployments, self.software = directory, deployments, software
        self.guard = threading.RLock()
        for path in (directory, directory/'groups', directory/'runs'):
            path.mkdir(mode=0o700, parents=True, exist_ok=True)
        for run in self.records():
            if run['status'] == 'running':
                run.update(status='interrupted', finished=Software.now())
                self.save_run(run)

    def group(self, identity):
        Software.validate_id(identity)
        return json.loads((self.directory/'groups'/(identity+'.json')).read_text())

    def groups(self):
        return sorted((json.loads(path.read_text()) for path in (self.directory/'groups').glob('*.json')),
                      key=lambda row: row['name'].casefold())

    def records(self):
        return sorted((json.loads(path.read_text()) for path in (self.directory/'runs').glob('*.json')),
                      key=lambda row: (row['started'], row['id']), reverse=True)

    def state(self):
        return dict(groups=self.groups(), modules=list(MODULES.values()), profiles=self.software.profiles(),
                    hosts=sorted(self.software.enrolled()), runs=self.records(), busy=self.deployments.lock.locked(),
                    parallelism=CLIENT_WORKERS)

    def resolve(self, steps):
        if (not isinstance(steps, list) or not 1 <= len(steps) <= 25
                or any(not isinstance(step, str) for step in steps) or len(set(steps)) != len(steps)):
            raise ValueError('Bitte 1 bis 25 unterschiedliche Bausteine auswählen.')
        resolved = []
        for step in steps:
            if step in MODULES:
                resolved.append(dict(MODULES[step]))
            elif step.startswith('software:'):
                profile = self.software.profile(step.removeprefix('software:'))
                debs=self.software.own.snapshot(profile['packages'])
                if profile.get('approved') != package_revision(profile['packages'],debs):
                    raise ValueError('Softwareprofil „'+profile['name']+'“ zuerst unter Repo Software am Pilotrechner abnehmen.')
                resolved.append(dict(id=step, label=profile['name'], packages=packages(profile['packages']), in_baseline=False))
                if debs:resolved[-1]['debs']=debs
            else:
                raise ValueError('Unbekannter Playbook-Baustein.')
        names = sorted({name for row in resolved for name in row.get('packages', [])})
        if names:
            packages(names)  # Enforce the shared 50-package limit for a group.
        return resolved

    def save_group(self, identity, name, steps):
        if (not isinstance(identity, str) or not isinstance(name, str) or not 1 <= len(name.strip()) <= 80
                or any(ord(char) < 32 for char in name)):
            raise ValueError('Bitte einen Gruppennamen mit höchstens 80 Zeichen angeben.')
        self.resolve(steps)
        with self.guard:
            if identity:
                self.group(identity)
            rows = self.groups()
            if not identity and len(rows) >= 50:
                raise ValueError('Höchstens 50 Playbook-Gruppen speichern.')
            if any(row['id'] != identity and row['name'].casefold() == name.strip().casefold() for row in rows):
                raise ValueError('Dieser Gruppenname ist bereits vergeben.')
            row = dict(id=identity or uuid.uuid4().hex, name=name.strip(), steps=steps, updated=Software.now())
            Software.atomic(self.directory/'groups'/(row['id']+'.json'), row)
            return row

    def delete_group(self, identity, confirmed):
        if confirmed is not True:
            raise ValueError('Löschen bestätigen.')
        with self.guard:
            self.group(identity)
            (self.directory/'groups'/(identity+'.json')).unlink()

    def snapshot(self, identity):
        with self.guard:
            row = self.group(identity)
            result = dict(id=row['id'], name=row['name'], steps=self.resolve(row['steps']))
            result['revision'] = hashlib.sha256(json.dumps(result, sort_keys=True).encode()).hexdigest()
            return result

    def fresh_selection(self, identity):
        """The fresh pipeline satisfies module steps once and merges extra packages."""
        return self.snapshot(identity) if identity else None

    def start(self, group_id, targets, confirmed):
        if confirmed is not True:
            raise ValueError('Gruppenauftrag ausdrücklich bestätigen.')
        if not self.deployments.lock.acquire(blocking=False):
            return None
        try:
            group = self.snapshot(group_id)
            selected = self.software.targets(targets, students_only=True)
            enrolled = self.software.enrolled()
            pins = {host: str(Path(enrolled[host]['known_hosts'])) for host in selected}
            run = dict(id=uuid.uuid4().hex, group=group, targets=selected, started=Software.now(), status='running',
                       pins=pins, pin_sha256={host: hashlib.sha256(Path(pin).read_bytes()).hexdigest()
                                             for host, pin in pins.items()}, results={}, active={})
            self.save_run(run)
            threading.Thread(target=self.execute, args=(run,), daemon=True).start()
            return dict(run)
        except BaseException:
            self.deployments.lock.release()
            raise

    def save_run(self, run):
        Software.atomic(self.directory/'runs'/(run['id']+'.json'), run)

    def on_host(self, host, run):
        pin = Path(run['pins'][host])
        if hashlib.sha256(pin.read_bytes()).hexdigest() != run['pin_sha256'][host]:
            raise ValueError('SSH-Hostschlüssel wurde seit dem Start geändert.')
        path = self.directory/'runs'/(run['id']+'-'+host+'.log')
        with path.open('w', buffering=1) as log, TemporaryDirectory(prefix='lab-group-ssh-') as directory:
            socket = Path(directory)/host
            try:
                for index, step in enumerate(run['group']['steps'], 1):
                    if reserved(host):
                        raise ValueError('Client ist für die Abendwartung reserviert.')
                    with self.guard:
                        run['active'][host] = step['label']
                        self.save_run(run)
                    log.write(f'\nSchritt {index}/{len(run["group"]["steps"])} · {step["label"]}\n')
                    if 'packages' in step:
                        # Reuse the existing package installer, including dependency
                        # planning, removal protection and the on-client status UI.
                        options={'debs':step['debs']} if step.get('debs') else {}
                        command = self.software.on_host_command(host, pin, step['packages'],**options)
                    else:
                        command = self.deployments.command(host, step['id'], release=True,
                                                           known_hosts=pin, control_socket=socket)
                    result = subprocess.run(command, cwd=ROOT, stdin=subprocess.DEVNULL, stdout=log,
                                            stderr=subprocess.STDOUT, timeout=3600)
                    if result.returncode:
                        raise ValueError('Schritt „'+step['label']+'“ fehlgeschlagen. Protokoll öffnen.')
                return dict(status='succeeded', message='Alle Bausteine abgeschlossen.')
            finally:
                close_control_socket(socket, host)

    def execute(self, run):
        try:
            with ThreadPoolExecutor(max_workers=CLIENT_WORKERS) as workers:
                pending = {workers.submit(self.on_host, host, run): host for host in run['targets']}
                for future in as_completed(pending):
                    host = pending[future]
                    try:
                        result = future.result()
                    except (OSError, ValueError, subprocess.SubprocessError) as error:
                        result = dict(status='failed', message=str(error))
                    with self.guard:
                        run['results'][host] = result
                        run['active'].pop(host, None)
                        self.save_run(run)
            run['status'] = 'succeeded' if all(row['status'] == 'succeeded' for row in run['results'].values()) else 'failed'
        finally:
            try:
                self.software.invalidate_inventory(run['targets'])
                run.update(finished=Software.now())
                if run['status'] == 'running':
                    run['status'] = 'failed'
                self.save_run(run)
            finally:
                self.deployments.lock.release()

    def output(self, identity, host):
        Software.validate_id(identity)
        run = json.loads((self.directory/'runs'/(identity+'.json')).read_text())
        if host not in run['targets']:
            raise ValueError('Client gehört nicht zu diesem Auftrag.')
        path = self.directory/'runs'/(identity+'-'+host+'.log')
        with path.open('rb') as output:
            output.seek(max(0, path.stat().st_size-65536))
            return output.read().decode(errors='replace')
