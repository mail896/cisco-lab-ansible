"""Software profiles through the existing, pinned, forced-command Teacher key."""
import hashlib
import json
import subprocess

from dashboard.admission import read_settings, write_private
from dashboard.deploy import ROOT
from maintenance import teacher_controller

HOST = 'LAB-Teacher'


class TeacherSoftware:
    def __init__(self, path):
        self.path = path

    @staticmethod
    def revision():
        return hashlib.sha256((ROOT/'dashboard/software_client.py').read_bytes()+
                              (ROOT/'dashboard/deb_client.py').read_bytes()).hexdigest()

    def state(self):
        try:
            data = read_settings(self.path)
            ready = (data.get('ready') is True and data.get('revision') == self.revision()
                     and data.get('pin') == hashlib.sha256(teacher_controller.PIN.read_bytes()).hexdigest())
            return dict(ready=ready, checked_at=data.get('checked_at'), error=data.get('error', ''))
        except (OSError, ValueError):
            return dict(ready=False, checked_at=None, error='')

    def verify(self):
        from datetime import datetime
        from maintenance.preview import BERLIN
        data = dict(ready=False, checked_at=datetime.now(BERLIN).isoformat(), error='')
        try:
            answer = self.decode(self.execute('capabilities'))
            if answer != dict(host=HOST, protocol=2, revision=self.revision()):
                raise ValueError('Teacher-Empfänger zuerst mit dem aktuellen Installer aktualisieren.')
            data.update(ready=True, revision=self.revision(),
                        pin=hashlib.sha256(teacher_controller.PIN.read_bytes()).hexdigest())
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            data['error'] = str(error) if isinstance(error, ValueError) else 'Teacher ist nicht erreichbar. Einschalten und erneut prüfen.'
        temporary = self.path.with_suffix('.tmp')
        write_private(temporary, json.dumps(data))
        temporary.replace(self.path)
        return self.state()

    def execute(self, operation, names=None, expected=None, *, debs=None, stderr=subprocess.PIPE):
        payload = dict(operation=operation)
        if operation != 'capabilities':
            if not self.state()['ready']:
                raise ValueError('Teacher-Softwarezugang zuerst unter Repo Software prüfen.')
            payload['revision'] = self.revision()
        if names is not None:
            payload['packages'] = names
        if expected is not None:
            payload['expected'] = expected
        if debs:
            payload['debs'] = debs
        return subprocess.run(teacher_controller.connection('software'), input=json.dumps(payload),
                              stdout=subprocess.PIPE, stderr=stderr, text=True,
                              timeout=3600 if operation == 'install' else 90 if operation == 'plan' else 25)

    def upload(self,row,path):
        from dashboard.deb_client import descriptors
        descriptors([row])
        with path.open('rb') as package:
            result=subprocess.run(teacher_controller.connection('software-upload '+row['sha256']+' '+str(row['size'])),
                                  stdin=package,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=600)
        data=self.decode(result)
        if data!=dict(received=row['sha256'],size=row['size']):raise ValueError('Teacher bestätigt die Paketübertragung nicht.')

    @staticmethod
    def decode(result):
        if result.returncode:
            raise ValueError('Teacher-Softwareauftrag fehlgeschlagen: ' +
                             ((result.stderr or '').strip()[-350:] or 'Protokoll öffnen; Teacher-Zugang gegebenenfalls erneut prüfen.'))
        try:
            data = json.loads(result.stdout)
        except ValueError:
            raise ValueError('Teacher lieferte keine gültige Softwareantwort.') from None
        if not isinstance(data, dict):
            raise ValueError('Teacher lieferte keine gültige Softwareantwort.')
        return data

    def inventory(self):
        data = self.decode(self.execute('inventory'))
        if set(data) != {'output'} or not isinstance(data['output'], str):
            raise ValueError('Teacher lieferte keinen gültigen Paketstand.')
        return data['output']
