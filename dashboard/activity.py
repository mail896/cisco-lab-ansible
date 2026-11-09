"""Small read-only summary of an active LAB job; never contacts clients."""
from datetime import datetime
import json
import threading
import time

from dashboard.deploy import JOBS, LAB_TARGETS
from dashboard.room import ACTIONS, SCOPES

MAX_RECORD_BYTES = 1024 * 1024


class Activity:
    def __init__(self, directory, operation_lock):
        self.directory, self.operation_lock = directory, operation_lock
        self.guard = threading.Lock()
        self.cached = None
        self.checked = 0

    def snapshot(self):
        with self.guard:
            if not self.operation_lock.locked():
                self.cached = None
                return {'busy': False}
            if self.cached and time.monotonic() - self.checked < 2:
                return self.cached
            candidates = []
            groups = (
                ('deploys', '*.json', 'Pilotauftrag', '/pilotdeploys'),
                ('provision', '*.json', 'Neuinstallation', '/installationsfolge'),
                ('provision/intake', 'read-*.json', 'Geräteprüfung', '/installationsfolge'),
                ('room', '*.json', 'Sammelaktion', '/sammelaktionen'),
                ('appearance-releases', '*.json', 'Raumverteilung', '/desktopbilder'),
                ('backup', '*.json', 'Sicherung', '/backup'),
                ('backup', 'cleanup-state', 'Alte Sicherungen bereinigen', '/backup'),
                ('maintenance', '*.json', 'Abendwartung', '/wartung'),
                ('teacher', 'run-*.json', 'Teacher-Wartung', '/wartung'),
                ('software/runs', '*.json', 'Softwareinstallation', '/repo-software'),
                ('playbook-groups/runs', '*.json', 'Playbook-Gruppe', '/playbooks'),
            )
            for folder, pattern, label, href in groups:
                paths = []
                for path in (self.directory / folder).glob(pattern):
                    try:
                        if not path.is_symlink():
                            paths.append((path.stat().st_mtime_ns, path))
                    except FileNotFoundError:
                        pass
                # A new job is among the latest files. Do not parse all history.
                for _, path in sorted(paths, reverse=True)[:16]:
                    try:
                        with path.open('rb') as source:
                            raw = source.read(MAX_RECORD_BYTES + 1)
                        if len(raw) > MAX_RECORD_BYTES:
                            continue
                        run = json.loads(raw)
                        if not isinstance(run, dict) or run.get('status') not in ('running', 'queued'):
                            continue
                        started = datetime.fromisoformat(run['started'])
                        if started.tzinfo is None:
                            continue
                        hosts = run.get('targets') if folder not in ('backup', 'teacher') else []
                        if not isinstance(hosts, list):
                            hosts = SCOPES.get(run.get('scope'), [])
                        hosts = [host for host in hosts if isinstance(host, str) and host in (*LAB_TARGETS, 'LAB-Teacher')]
                        if run.get('host') in LAB_TARGETS:
                            hosts.append(run['host'])
                        if folder == 'teacher':
                            hosts = ['LAB-Teacher']
                        title = label
                        if folder == 'playbook-groups/runs':
                            title += ': ' + run.get('group', {}).get('name', 'Gruppe')
                        if folder == 'software/runs' and run.get('kind') == 'plan':
                            title = 'Softwareinstallation prüfen'
                        if folder == 'room':
                            title = ACTIONS.get(run.get('action'), label)
                        elif folder == 'maintenance' and run.get('manual_start'):
                            title = 'Manuelle Wartung'
                        elif folder == 'deploys':
                            title += ': ' + JOBS.get(run.get('job'), (label,))[0]
                        elif folder == 'provision' and run.get('kind') == 'bootstrap':
                            title = 'SSH-Erstaufnahme'
                        if folder == 'appearance-releases' and run.get('job', 'appearance') != 'appearance':
                            href = '/playbooks'
                        candidates.append((started.timestamp(), dict(
                            busy=True, label=title, hosts=list(dict.fromkeys(hosts)),
                            started=run['started'], href=href)))
                    except (OSError, ValueError, KeyError, TypeError):
                        continue
            result = max(candidates, key=lambda row: row[0])[1] if candidates else {
                'busy': True, 'label': 'Verwaltungsauftrag läuft', 'hosts': [], 'started': None, 'href': None,
            }
            if result.get('label') in {'Neuinstallation', 'SSH-Erstaufnahme', 'Geräteprüfung'}:
                related = [row for _, row in candidates if row['label'] == result['label']]
                result['hosts'] = sorted({host for row in related for host in row['hosts']})
                result['started'] = min(related, key=lambda row: datetime.fromisoformat(row['started']))['started']
            if not self.operation_lock.locked():
                result = {'busy': False}
            self.cached, self.checked = result, time.monotonic()
            return result
