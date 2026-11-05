"""Fixed-target NetworkManager backend. All external effects are injectable in tests."""
import configparser
import copy
import hashlib
import json
import logging
import os
import shutil
from pathlib import Path
import subprocess
import time
import uuid

from network.collect_interfaces import collect
from network.security.lab_request import keyfile, validate_identity, validate_request
from network.security.native_keyfile import verify as verify_keyfile
from network.broker.storage import atomic_write, operation_lock, trusted_file

LOG = logging.getLogger('cisco-lab-network')
NM = '/usr/bin/nmcli'
IP = '/usr/sbin/ip'
CLEAR = {'action':'clear', 'addresses':[], 'routes':[]}


class CommandError(RuntimeError):
    pass


def run_command(args):
    result = subprocess.run(args, capture_output=True, text=True, timeout=25,
                            env={'PATH':'/usr/sbin:/usr/bin:/sbin:/bin','LC_ALL':'C'})
    if result.returncode:
        LOG.error('Command failed: %s: %s', args[:4], result.stderr.strip())
        raise CommandError('Netzwerkvorgang fehlgeschlagen. Bitte die Lehrkraft informieren.')
    return result.stdout


class Backend:
    def __init__(self, identity, profiles=Path('/etc/NetworkManager/system-connections'),
                 state=Path('/var/lib/cisco-lab-network'), backups=Path('/var/backups/cisco-lab/network'),
                 lock=Path('/run/cisco-lab-network/operation.lock'), run=run_command,
                 hardware=collect):
        validate_identity(identity)
        self.identity = copy.deepcopy(identity)
        self.profiles, self.state, self.backups, self.lock = map(Path, (profiles,state,backups,lock))
        self.run, self.hardware = run, hardware

    def resolved(self, allow_missing_lab=False):
        devices = self.hardware()
        identity = copy.deepcopy(self.identity)
        for kind in ('school','lab'):
            desired = identity[kind]
            matches = [d for d in devices if not d.get('error') and not d.get('wireless')
                       and d.get('type') == '1' and d['current_mac'].lower() == desired['mac'].lower()
                       and d['usb'] is desired['usb']]
            if not matches and kind == 'lab' and allow_missing_lab:
                identity['lab']['interface'] = None
                continue
            if len(matches) != 1:
                raise ValueError('Adapterzuordnung nicht eindeutig: ' + kind + '. Bitte die Lehrkraft informieren.')
            # Names may differ between adapters and USB ports; match hardware, not prefix.
            desired['interface'] = matches[0]['interface']
        if identity['lab']['interface'] == identity['school']['interface']:
            raise ValueError('Schul- und Laboradapter duerfen nicht identisch sein.')
        return identity

    def uuids(self, active=False):
        args = [NM,'-t','-f','UUID','connection','show'] + (['--active'] if active else [])
        return set(self.run(args).splitlines())

    def profile_path(self, kind):
        wanted = uuid.UUID(self.identity[kind]['uuid'])
        matches = []
        for path in sorted(self.profiles.iterdir()):
            # IServ's installer can leave an active keyfile without an extension.
            if path.name.startswith('.') or path.suffix not in ('', '.nmconnection'):
                continue
            if path.is_symlink() or not path.is_file():
                raise ValueError('Kein regulaeres NM-Verwaltungsprofil; zuerst administrativ klaeren.')
            config = configparser.ConfigParser(interpolation=None)
            config.read_string(path.read_text())
            value = config.get('connection','uuid',fallback='')
            if value and uuid.UUID(value) == wanted:
                info = path.stat()
                if info.st_uid != os.geteuid() or info.st_mode & 0o077:
                    raise ValueError('Unsichere Rechte am verwalteten Netzwerkprofil.')
                matches.append(path)
        if len(matches) > 1:
            raise ValueError('Mehrere Dateien fuer dieselbe Profil-UUID; zuerst administrativ klaeren.')
        if matches:
            return matches[0]
        if str(wanted) in self.uuids():
            raise ValueError('Profil ist geladen, aber nicht eindeutig gespeichert; Migration zuerst klaeren.')
        return self.profiles / ('cisco-lab-' + kind + '.nmconnection')

    def school_guard(self, identity):
        signatures = []
        has_default = False
        for version in ('-4','-6'):
            routes = json.loads(self.run([IP,version,'-j','route','show','table','main']))
            for route in routes:
                device = route.get('dev')
                destination = route.get('dst','default')
                if destination in ('default','0.0.0.0/0','::/0'):
                    if device != identity['school']['interface']:
                        raise ValueError('Default-Route liegt nicht ausschliesslich am Schulanschluss.')
                    if version == '-4': has_default = True
                if device == identity['school']['interface']:
                    signatures.append((version, destination, route.get('gateway',''),device,route.get('metric',0)))
        if not has_default:
            raise ValueError('Schulnetz hat keine IPv4-Default-Route. Bitte auf Netzwerk-Heilung warten.')
        return sorted(signatures)

    def backup(self, path, action, was_active):
        name = time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()) + '-' + uuid.uuid4().hex
        self.backups.mkdir(parents=True,exist_ok=True,mode=0o700)
        used=sum(file.stat().st_size for file in self.backups.rglob('*') if file.is_file() and not file.is_symlink())
        if used > 100*1024*1024 or shutil.disk_usage(self.backups).free < 256*1024*1024:
            raise ValueError('Nicht genug Platz fuer eine sichere Netzwerksicherung. Bitte die Lehrkraft informieren.')
        directory = self.backups / name
        directory.mkdir(parents=True, mode=0o700)
        old = path.read_text() if path.exists() else None
        atomic_write(directory/'before.json', json.dumps({'path':str(path),'existed':old is not None,
                     'was_active':was_active,'action':action,'uuid':self.identity['lab' if action != 'school-heal' else 'school']['uuid']}))
        if old is not None: atomic_write(directory/'profile.before',old)
        return old, directory

    def _apply(self, request, uid):
        request = validate_request(request,self.identity)
        identity = self.resolved()
        before_routes = self.school_guard(identity)
        path = self.profile_path('lab')
        lab_uuid = str(uuid.UUID(identity['lab']['uuid']))
        content = keyfile(request,identity)
        verify_keyfile(content)
        was_active = lab_uuid in self.uuids(active=True)
        carrier = self.run([NM, '-g', 'WIRED-PROPERTIES.CARRIER', 'device',
                            'show', identity['lab']['interface']]).strip()
        can_activate = carrier != 'off'
        if path.exists() and path.read_text()==content and (was_active or not can_activate):
            digest=hashlib.sha256(path.read_bytes()).hexdigest()
            atomic_write(self.state/'last-request.json',json.dumps({'request':request,'sha256':digest}))
            return {'ok':True,'configuration':request,'interface':identity['lab']['interface']}
        old, backup = self.backup(path,request['action'],was_active)
        activated = False
        try:
            if request['action'] == 'clear' and was_active:
                self.run([NM,'--wait','15','connection','down','uuid',lab_uuid])
            self.run([NM,'device','set',identity['lab']['interface'],'autoconnect','no'])
            atomic_write(path,content)
            self.run([NM,'--wait','15','connection','load',str(path)])
            if can_activate:
                activated = True  # also activate a neutral layer-2 profile without IP
                self.run([NM,'--wait','15','connection','up','uuid',lab_uuid,'ifname',identity['lab']['interface']])
            if self.school_guard(identity) != before_routes:
                raise ValueError('Schulrouten haben sich geaendert; Laborvorgang wird zurueckgenommen.')
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            atomic_write(self.state/'last-request.json',json.dumps({'request':request,'sha256':digest}))
            atomic_write(backup/'result.json',json.dumps({'result':'ok','uid':uid}))
            LOG.info('LAB operation %s uid=%s complete', request['action'], uid)
        except Exception:
            try:
                if activated and lab_uuid in self.uuids(active=True):
                    self.run([NM,'--wait','15','connection','down','uuid',lab_uuid])
                if old is not None:
                    atomic_write(path,old)
                    self.run([NM,'--wait','15','connection','load',str(path)])
                    if was_active:
                        self.run([NM,'--wait','15','connection','up','uuid',lab_uuid,'ifname',identity['lab']['interface']])
                else:
                    if lab_uuid in self.uuids():
                        self.run([NM,'--wait','15','connection','delete','uuid',lab_uuid])
                    if path.exists(): path.unlink()
                atomic_write(backup/'result.json',json.dumps({'result':'rolled-back','uid':uid}))
            except Exception:
                LOG.exception('LAB rollback failed; backup at %s',backup)
                raise CommandError('Laborvorgang und Ruecknahme fehlgeschlagen. Bitte die Lehrkraft informieren; Sicherung vorhanden.')
            raise
        return {'ok':True,'configuration':request,'interface':identity['lab']['interface']}

    def apply(self, request, uid):
        with operation_lock(self.lock):
            return self._apply(request,uid)

    def _status(self):
        identity = self.resolved()
        path = self.profile_path('lab')
        saved = self.state/'last-request.json'
        configuration = None
        if saved.exists() and path.exists():
            value = json.loads(saved.read_text())
            if value['sha256'] == hashlib.sha256(path.read_bytes()).hexdigest():
                configuration = validate_request(value['request'],identity)
        return {'ok':True,'interface':identity['lab']['interface'],
                'active':str(uuid.UUID(identity['lab']['uuid'])) in self.uuids(active=True),
                'configuration':configuration,
                'notice':'' if configuration is not None else 'Bestehendes Profil nicht vom LAB-Dialog bestaetigt; vor Uebernahme pruefen.'}

    def status(self):
        with operation_lock(self.lock): return self._status()

    def _seen(self):
        path = self.state/'sessions.json'
        return json.loads(path.read_text()) if path.exists() else []

    def ensure_session(self, token, uid):
        with operation_lock(self.lock):
            seen = self._seen()
            if token not in seen:
                self._apply(CLEAR,uid)
                atomic_write(self.state/'sessions.json',json.dumps((seen+[token])[-128:]))

    def _repair_school(self, identity):
        school = identity['school']; identifier = str(uuid.UUID(school['uuid']))
        if identifier not in self.uuids():
            path = self.profile_path('school')
            self.backup(path,'school-heal',False)
            config = f'''[connection]\nid=LAN-Schule\nuuid={identifier}\ntype=ethernet\ninterface-name={school['interface']}\nautoconnect=true\n\n[ethernet]\nmac-address={school['mac']}\nwake-on-lan=64\n\n[ipv4]\nmethod=auto\nnever-default=false\nroute-metric=100\n\n[ipv6]\nmethod=disabled\n'''
            verify_keyfile(config)
            atomic_write(path,config)
            self.run([NM,'--wait','15','connection','load',str(path)])
            self.run([NM,'--wait','15','connection','up','uuid',identifier,'ifname',school['interface']])
            return
        fields = ['ipv4.method','ipv4.never-default','ipv6.method','connection.autoconnect',
                  'connection.interface-name','802-3-ethernet.mac-address','802-3-ethernet.wake-on-lan',
                  'ipv4.addresses','ipv4.gateway','ipv4.routes','ipv4.dns','ipv4.dns-search']
        values = self.run([NM,'--escape','no','-g',','.join(fields),'connection','show','uuid',identifier]).splitlines()
        correct = (len(values)==12 and values[:4] == ['auto','no','disabled','yes']
                   and values[4]==school['interface'] and values[5].lower()==school['mac'].lower()
                   and values[6] in ('magic','64') and values[7:]==['']*5)
        try: self.school_guard(identity)
        except ValueError: correct = False
        if correct and identifier in self.uuids(active=True): return
        path = self.profile_path('school')
        self.backup(path,'school-heal',identifier in self.uuids(active=True))
        self.run([NM,'connection','modify','uuid',identifier,
                  'connection.interface-name',school['interface'],'802-3-ethernet.mac-address',school['mac'],
                  '802-3-ethernet.wake-on-lan','magic','connection.autoconnect','yes',
                  'ipv4.method','auto','ipv4.addresses','','ipv4.gateway','','ipv4.routes','',
                  'ipv4.dns','','ipv4.dns-search','','ipv4.never-default','no','ipv4.route-metric','100',
                  'ipv6.method','disabled'])
        self.run([NM,'--wait','15','connection','up','uuid',identifier,'ifname',school['interface']])

    def health(self):
        with operation_lock(self.lock):
            identity=self.resolved()
            self.school_guard(identity)
            path=self.profile_path('lab')
            if not path.exists() or identity['lab']['uuid'] not in self.uuids():
                raise ValueError('Laborprofil ist noch nicht einsatzbereit.')
            return {'ok':True}

    def heal(self):
        with operation_lock(self.lock):
            identity = self.resolved(allow_missing_lab=True)
            self._repair_school(identity)
            if identity['lab']['interface'] is None: return
            self.run([NM,'device','set',identity['lab']['interface'],'autoconnect','no'])
            path = self.profile_path('lab')
            if not path.exists():
                self._apply(CLEAR, 0)
                return
            if identity['lab']['uuid'] in self.uuids(active=True):
                return
            carrier = self.run([NM, '-g', 'WIRED-PROPERTIES.CARRIER', 'device',
                                'show', identity['lab']['interface']]).strip()
            if carrier == 'on':
                # Reuse the validated exercise after USB reconnect, never a
                # legacy or externally modified profile.
                configuration = self._status()['configuration']
                if configuration is not None:
                    self._apply(configuration, 0)
