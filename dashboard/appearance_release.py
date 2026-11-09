"""Distribute only the exact Ansible configuration accepted on a LAB pilot."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import uuid

from dashboard.deploy import (APPEARANCE_PROBE, CISCO_FILES, GREETER_CURRENT, IMAGE_CURRENT,
                              JOBS, LAB_TARGETS, PILOTS, ROOT, close_control_socket)
from dashboard.greeter_refresh import refresh_greeter
from maintenance.preview import BERLIN
from maintenance.reservations import reserved

JOB = 'appearance'
SKIP = ('Lokale Benutzersitzung vorhanden.', 'Lokale Sitzung aktiv oder nicht eindeutig.',
        'IServ-Home ist noch eingebunden.',
        'läuft oder der Status ist nicht eindeutig.')


class AppearanceRelease:
    def __init__(self, directory, deployments):
        self.directory = directory
        self.deployments = deployments
        directory.mkdir(mode=0o700,parents=True,exist_ok=True)
        for path in directory.glob('*.json'):
            run = json.loads(path.read_text())
            if run['status']=='running':
                run['status']='interrupted'
                self.save(run)

    def save(self, run):
        path = self.directory/(run['id']+'.json')
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(run,ensure_ascii=False))
        temporary.replace(path)

    def history(self, job=JOB, limit=12):
        runs = [json.loads(path.read_text()) for path in self.directory.glob('*.json')]
        return sorted((run for run in runs if run.get('job', JOB)==job),
                      key=lambda run: run['started'],reverse=True)[:limit]

    def approval(self, job=JOB):
        if job not in JOBS:
            raise ValueError('Unbekannter LAB-Auftrag.')
        revision = self.deployments.revision(job)
        for pilot in self.deployments.history(limit=None):
            if (pilot['job'] != job or pilot['host'] not in PILOTS or pilot['revision'] != revision
                    or pilot['status'] != 'succeeded' or pilot.get('source_changed') or not pilot.get('review')):
                continue
            return pilot if pilot['review']['decision']=='accepted' else None
        return None

    @staticmethod
    def pending(run):
        return [host for host in run.get('targets', LAB_TARGETS)
                if not run['results'].get(host, '').startswith(('Bild installiert.', 'Deploy erfolgreich.', 'Abgenommener Stand bereits installiert.'))]

    def state(self, job=JOB, limit=12):
        approved = self.approval(job)
        runs = self.history(job, limit=limit) if limit is None else self.history(job)
        for run in runs:
            run['pending'] = self.pending(run)
        return dict(approval=approved, targets=list(LAB_TARGETS), runs=runs)

    def start(self, run_id, refresh, confirmed, *, job=JOB, retry=''):
        if type(refresh) is not bool or confirmed is not True:
            raise ValueError('Raumverteilung ausdrücklich bestätigen.')
        if not self.deployments.lock.acquire(blocking=False):
            return None
        try:
            approved = self.approval(job)
            if not approved or approved['id'] != run_id:
                raise ValueError('Für diesen Auftrag und Quellenstand liegt keine aktuelle Pilotabnahme vor.')
            targets = list(LAB_TARGETS)
            if retry:
                self.deployments.validate_id(retry)
                previous = json.loads((self.directory/(retry+'.json')).read_text())
                if (previous.get('job', JOB)!=job or previous['revision']!=approved['revision']
                        or previous['status']=='running'):
                    raise ValueError('Nachholen ist nur für denselben freigegebenen Stand möglich.')
                targets = self.pending(previous)
                if not targets or any(host not in LAB_TARGETS for host in targets):
                    raise ValueError('Keine ausstehenden LAB-Ziele.')
            run = dict(id=uuid.uuid4().hex,pilot=run_id,job=job,revision=approved['revision'],
                       refresh=job==JOB and refresh,started=datetime.now(BERLIN).isoformat(timespec='seconds'),
                       status='running',targets=targets,results={},active=[])
            self.save(run)
            threading.Thread(target=self.execute,args=(run,),daemon=True).start()
            return dict(run)
        except BaseException:
            self.deployments.lock.release()
            raise

    def stage(self, checkout, revision, job=JOB):
        shutil.copytree(ROOT/'roles',checkout/'roles',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
        required = ('dashboard-pilot.yml','inventory.ini','dashboard/appearance-tasks.yml',JOBS[job][2])
        if job == JOB:
            required += (APPEARANCE_PROBE,)
        if job == 'cisco':
            required += CISCO_FILES
        for relative in dict.fromkeys(required):
            path = checkout/relative
            path.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(ROOT/relative,path)
            if relative.startswith('local-private/'):
                path.chmod(0o600)
        staged = {}
        if job in ('appearance','session'):
            for kind,(label,source) in self.deployments.appearance.active().items():
                path = checkout/(kind+source.suffix)
                shutil.copyfile(source,path)
                staged[kind] = (label,path)
        if self.deployments.revision(job,root=checkout,staged_images=staged) != revision:
            raise ValueError('Kopierte Quellen stimmen nicht mit der Pilotabnahme überein.')
        return staged

    def on_host(self, host, checkout, images, refresh, log_file, control_socket, job):
        if reserved(host):
            return 'Übersprungen: Für automatische Wartung reserviert.'
        try:
            command = self.deployments.command(host,job,release=True,rollout_images=images,control_socket=control_socket)
            try:
                result = subprocess.run(command,cwd=checkout,text=True,capture_output=True,timeout=400)
            finally:
                close_control_socket(control_socket, host)
            with log_file.open('w') as output:
                output.write(result.stdout)
                output.write(result.stderr)
            if result.returncode:
                for reason in SKIP:
                    if reason in result.stdout:
                        return f'Übersprungen: {reason}'
                return 'Fehler: Deploy nicht erfolgreich. Ansible-Protokoll prüfen.'
            already_current = job == JOB and IMAGE_CURRENT in result.stdout
            status = 'Bild installiert. Bildstand war bereits aktuell.' if already_current else 'Bild installiert.'
            if refresh and GREETER_CURRENT not in result.stdout:
                known = self.deployments.known_hosts_for(host)
                try:
                    return status + ' ' + refresh_greeter(host,LAB_TARGETS[host],known)
                except (OSError,subprocess.SubprocessError):
                    return status + ' SDDM-Neustart konnte nicht bestätigt werden.'
            if already_current:
                return status
            return 'Bild installiert. Sichtbar beim nächsten SDDM-Start.' if job==JOB else 'Deploy erfolgreich.'
        except (OSError,ValueError,subprocess.SubprocessError) as error:
            return f'Fehler: {error}'

    def output(self, run_id, host):
        self.deployments.validate_id(run_id)
        if host not in LAB_TARGETS:
            raise FileNotFoundError
        path = self.directory/(run_id+'-'+host+'.log')
        with path.open('rb') as file:
            file.seek(max(0,path.stat().st_size-65536))
            return file.read().decode(errors='replace')

    def execute(self, run):
        try:
            with (
                tempfile.TemporaryDirectory(dir=self.directory,prefix='.release-') as directory,
                tempfile.TemporaryDirectory(prefix='lab-room-ssh-') as ssh_directory,
            ):
                checkout = Path(directory)
                images = self.stage(checkout,run['revision'],run['job'])
                progress_lock = threading.Lock()

                def work(host):
                    with progress_lock:
                        run['active'].append(host)
                        self.save(run)
                    return self.on_host(host,checkout,images,run['refresh'],
                                        self.directory/(run['id']+'-'+host+'.log'),
                                        Path(ssh_directory)/host,run['job'])

                workers = 8 if run['job'] == JOB else 6
                with ThreadPoolExecutor(max_workers=workers) as pool:
                    jobs = {pool.submit(work,host):host for host in run['targets']}
                    for job in as_completed(jobs):
                        host = jobs[job]
                        result = job.result()
                        with progress_lock:
                            run['results'][host] = result
                            run['active'].remove(host)
                            self.save(run)
            run['status']='finished'
        except (OSError,ValueError,subprocess.SubprocessError) as error:
            run.update(status='failed',error=str(error))
        finally:
            try:
                if run['status']=='running':
                    run['status']='interrupted'
                run['finished']=datetime.now(BERLIN).isoformat(timespec='seconds')
                self.save(run)
            finally:
                self.deployments.lock.release()
