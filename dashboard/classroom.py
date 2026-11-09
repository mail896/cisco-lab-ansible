"""Shared on-demand classroom network snapshots, with no background scheduler."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from datetime import datetime, timezone
import json
import subprocess
import threading
import time

from dashboard.deploy import LAB_TARGETS, ROOT
from maintenance.controller import ssh, targets

INTERVAL = 60
WORKERS = 16
ERRORS = dict(identity='Die Clientidentität passt nicht zur Aufnahme.',
              manifest='Geprüfte LAB-Adapterzuordnung fehlt. Clientaufnahme prüfen.',
              **{'adapter-identity':'Der LAB-Adapter ist nicht eindeutig zugeordnet.'},
              read='Netzwerkstatus konnte nicht vollständig gelesen werden.')


def probe(host, record):
    command = ssh(host,record,['sudo','-n','/usr/bin/python3','-IB','-c',
                              (ROOT/'dashboard/classroom_client.py').read_text(),host])
    result = subprocess.run(command,capture_output=True,text=True,timeout=15)
    if result.returncode or len(result.stdout)>1048576:
        return dict(state='unreachable',error='Client nicht erreichbar oder lesender APP-Zugang nicht verfügbar.')
    try:
        value = json.loads(result.stdout)
        if not value.get('ok'):
            return dict(state='error',error=ERRORS.get(value.get('code'),ERRORS['read']))
        data = value['data']
        if value['host']!=host or data['state'] not in ('up','down','missing'):
            raise ValueError
        if data['state']!='missing' and any(not isinstance(data[key],list) for key in ('ipv4','ipv6','routes4','routes6')):
            raise ValueError
        users = data.get('users')
        if users is not None and (not isinstance(users,list) or any(
                not isinstance(user,dict) or not isinstance(user.get('account'),str) or not user['account']
                or user.get('kind') not in ('iserv','local','unknown') or not isinstance(user.get('locked'),bool)
                for user in users)):
            raise ValueError
        return data
    except (ValueError,KeyError,TypeError,AttributeError):
        return dict(state='error',error=ERRORS['read'])


class Classroom:
    def __init__(self, read=probe, enrolled=targets, clock=time.monotonic):
        self.read,self.enrolled,self.clock = read,enrolled,clock
        self.guard = threading.Lock()
        self.checked = None
        self.checked_at = None
        self.duration = None
        self.refreshing = False
        self.error = ''
        self.clients = {host:dict(state='pending') for host in LAB_TARGETS}

    def snapshot(self):
        with self.guard:
            if not self.refreshing and (self.checked is None or self.clock()-self.checked>=INTERVAL):
                self.refreshing = True
                try:
                    threading.Thread(target=self.refresh,daemon=True).start()
                except BaseException:
                    self.refreshing = False
                    raise
            age = None if self.checked is None else max(0,self.clock()-self.checked)
            return dict(clients=deepcopy(self.clients),refreshing=self.refreshing,error=self.error,
                        checked_at=self.checked_at,age_seconds=age,interval_seconds=INTERVAL,
                        duration_seconds=self.duration)

    def refresh(self):
        started = self.clock()
        error = ''
        rows = {host:dict(state='unmanaged') for host in LAB_TARGETS}
        try:
            enrolled = {host:record for host,record in self.enrolled().items() if host in LAB_TARGETS}
            for host in enrolled:
                rows[host] = dict(state='pending')
            with ThreadPoolExecutor(max_workers=WORKERS,thread_name_prefix='lab-lesson') as pool:
                pending = {pool.submit(self.read,host,record):host for host,record in enrolled.items()}
                for future in as_completed(pending):
                    host = pending[future]
                    try:
                        rows[host] = future.result()
                    except (OSError,ValueError,subprocess.SubprocessError):
                        rows[host] = dict(state='unreachable',error=f'Client derzeit nicht erreichbar. Nächste Prüfung in {INTERVAL} Sekunden.')
                    rows[host]['checked_at'] = datetime.now(timezone.utc).isoformat()
                    with self.guard:
                        self.clients[host] = deepcopy(rows[host])
        except (OSError,ValueError,KeyError):
            error = 'Aufgenommene Clients konnten nicht gelesen werden. Aufnahme prüfen.'
            rows = {host:dict(state='error',error=error) for host in LAB_TARGETS}
        finally:
            with self.guard:
                self.clients,self.error = rows,error
                self.checked,self.checked_at = self.clock(),datetime.now(timezone.utc).isoformat()
                self.duration = round(max(0, self.checked-started), 2)
                self.refreshing = False
