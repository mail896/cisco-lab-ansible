"""Shared saved client reports and one refresh after completed maintenance."""
from collections import Counter
from datetime import datetime
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import threading

from dashboard.deploy import ADMISSIONS
from dashboard.runtime import ROOT
from dashboard.targets import HOSTS
from maintenance.preview import BERLIN, start_allowed


class Dashboard:
    def __init__(self, report, known_hosts, revision_path=None):
        self.report = report
        self.known_hosts = known_hosts
        self.revision_path = revision_path
        self.attempted_revision = None
        self.refresh_lock = threading.Lock()
        self.refresh_error = None

    def report_revision(self):
        try:
            revision = self.revision_path.read_text().strip() if self.revision_path else ''
        except OSError:
            return None
        return revision if re.fullmatch(r'[a-f0-9]{32}', revision) else None

    def state(self):
        try:
            report = json.loads(self.report.read_text())
        except (OSError, ValueError):
            report = {}
        if not isinstance(report, dict):
            report = {}
        revision = self.report_revision()
        if (revision and revision != report.get('maintenance_revision')
                and revision != self.attempted_revision):
            self.refresh()
        clients = report.get('clients', {})
        rows = []
        for host in HOSTS:
            client = clients.get(host, {})
            upgrade = client.get('upgrade', {})
            cleanup = client.get('autoremove', {})
            apt = client.get('automatic_apt') or {}
            rows.append(dict(host=host, action=client.get('action', 'unknown'),
                             updates=len(upgrade.get('install', [])),
                             updates_known=isinstance(upgrade.get('install'), list),
                             update_packages=upgrade.get('install', []),
                             removals=len(cleanup.get('remove', [])),
                             removals_known=isinstance(cleanup.get('remove'), list),
                             removal_packages=cleanup.get('remove', []),
                             reason=client.get('reason', ''),
                             cleanup=client.get('autoremove_allowed', False),
                             patch=client.get('patch', 'unknown'),
                             apt_mode='central' if apt.get('periodic_unattended') == '0' else
                                      'local' if apt.get('upgrade_timer') == 'enabled' else
                                      'disabled' if apt.get('upgrade_timer') in ('disabled','masked') else 'unknown',
                             apt_enabled=apt.get('upgrade_timer') == 'enabled'))
        return dict(timestamp=report.get('timestamp'), window=start_allowed(datetime.now(BERLIN)),
                    counts=dict(Counter(row['action'] for row in rows)), clients=rows,
                    refreshing=self.refresh_lock.locked(), refresh_error=self.refresh_error)

    def refresh(self):
        if not self.refresh_lock.acquire(blocking=False):
            return False
        self.refresh_error = None
        revision = self.report_revision()
        self.attempted_revision = revision
        threading.Thread(target=self._refresh, args=(revision,), daemon=True).start()
        return True

    def _refresh(self, revision=None):
        try:
            command = [sys.executable, '-B', '-m', 'maintenance.preview', '--pin-directory', str(ADMISSIONS)]
            if self.known_hosts:
                command += ['--lab08b-known-hosts', str(self.known_hosts)]
            result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True,
                                    timeout=285, check=True)
            data = json.loads(result.stdout)
            data['maintenance_revision'] = revision
            with tempfile.NamedTemporaryFile(dir=self.report.parent, delete=False,
                                             mode='w', encoding='utf-8') as file:
                temporary = Path(file.name)
                try:
                    json.dump(data, file, ensure_ascii=False, indent=2)
                    file.write('\n')
                    file.flush()
                    temporary.replace(self.report)
                finally:
                    temporary.unlink(missing_ok=True)
        except (OSError, ValueError, subprocess.SubprocessError):
            self.refresh_error = 'Vorschau konnte nicht aktualisiert werden. Letzter Bericht bleibt erhalten.'
        finally:
            self.refresh_lock.release()


