"""Repository package catalogue, reusable profiles and reviewed LAB installs."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import closing
from datetime import datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import sqlite3
import subprocess
import threading
import uuid

import yaml

from dashboard.admission import read_settings, write_private
from dashboard.deploy import LAB_TARGETS, ROOT
from dashboard.runtime import ansible_tool
from dashboard.software_client import packages
from dashboard.own_packages import OwnPackages
from dashboard.teacher_software import HOST as TEACHER, TeacherSoftware
from maintenance.preview import BERLIN, CLIENT_WORKERS
from maintenance.reservations import reserved

SOFTWARE_WORKERS = CLIENT_WORKERS
MAX_RUN_BYTES = 16 * 1024 * 1024


def package_revision(names,debs=None):
    value=packages(names)
    if debs:value=[value,debs]
    return hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()


def client_source():
    return ('import sys,types\n_deb=types.ModuleType("deb_client")\n'
            'exec('+repr((ROOT/'dashboard/deb_client.py').read_text())+',_deb.__dict__)\n'
            'sys.modules["deb_client"]=_deb\n'+(ROOT/'dashboard/software_client.py').read_text())


def baseline_packages(directory=None):
    directory = directory or Path.home()/'.local/state/cisco-lab-dashboard/software/profiles'
    selected = set()
    own=OwnPackages(directory.parent/'packages')
    for path in directory.glob('*.json'):
        profile = read_settings(path)
        if profile.get('baseline'):
            names = packages(profile['packages'])
            if profile.get('approved') != package_revision(names,own.snapshot(names)):
                raise ValueError('Ein Baseline-Softwareprofil braucht eine erneute Pilotabnahme.')
            selected.update(names)
    return packages(sorted(selected)) if selected else []


class Software:
    def __init__(self, directory, deployments):
        self.directory, self.deployments = directory, deployments
        for path in (directory, directory/'profiles', directory/'runs'):
            path.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.guard = threading.RLock()
        self.catalog_guard = threading.Lock()
        self.catalog_error = ''
        self.inventory_guard = threading.Lock()
        self.inventory = {}
        self.inventory_epoch = {}
        self.teacher = TeacherSoftware(directory/'teacher-access.json')
        self.own=OwnPackages(directory/'packages')
        for run in self.records():
            if run['status']=='running':
                run.update(status='interrupted',finished=self.now())
                self.save(run)

    @staticmethod
    def now():
        return datetime.now(BERLIN).isoformat(timespec='seconds')

    @staticmethod
    def validate_id(value):
        if not isinstance(value,str) or not re.fullmatch(r'[a-f0-9]{32}',value):
            raise ValueError('Ungültiger Softwareauftrag.')
        return value

    @staticmethod
    def atomic(path, data):
        temporary = path.with_name('.'+uuid.uuid4().hex+'.tmp')
        try:
            write_private(temporary,json.dumps(data,ensure_ascii=False))
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)

    def catalog(self):
        try:
            with closing(sqlite3.connect('file:'+str(self.directory/'catalog.sqlite')+'?mode=ro',uri=True)) as db:
                meta = json.loads(db.execute('SELECT value FROM meta').fetchone()[0])
        except (OSError,sqlite3.Error,ValueError,TypeError):
            meta = dict(count=0,release='Debian 13 · trixie',architecture='amd64',lists_at=None)
        return dict(meta,indexing=self.catalog_guard.locked(),error=self.catalog_error)

    def refresh_catalog(self):
        if not self.catalog_guard.acquire(blocking=False):
            return False
        def build():
            temporary = self.directory/('catalog-'+uuid.uuid4().hex+'.sqlite')
            try:
                self.catalog_error = ''
                # A client's signed Debian lists include contrib/non-free that
                # APP's own package lists may not contain. Transfer metadata only.
                from dashboard.provision import ssh_command
                enrolled = self.enrolled()
                result = None
                if enrolled:
                    host = 'LAB-08A' if 'LAB-08A' in enrolled else sorted(enrolled)[0]
                    command = ssh_command(host,Path(enrolled[host]['known_hosts']),
                        ['/usr/bin/python3','-B','-c',(ROOT/'dashboard/software_catalog.py').read_text(),'--export'])
                    command.insert(1,'-C')
                    with subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL) as remote:
                        try:
                            result = subprocess.run(['/usr/bin/python3','-B',str(ROOT/'dashboard/software_catalog.py'),
                                                     '--import',str(temporary)],stdin=remote.stdout,
                                                    stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,timeout=90)
                            remote.stdout.close()
                            if remote.wait(timeout=15):
                                result = None
                        finally:
                            remote.stdout.close()
                            if remote.poll() is None:
                                remote.kill()
                                remote.wait()
                if result is None or result.returncode:
                    if (self.directory/'catalog.sqlite').exists():
                        raise ValueError('Katalogclient derzeit nicht erreichbar.')
                    temporary.unlink(missing_ok=True)
                    result = subprocess.run(['/usr/bin/python3','-B',str(ROOT/'dashboard/software_catalog.py'),str(temporary)],
                                            stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,text=True,timeout=120,
                                            env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1'))
                if result.returncode:
                    raise ValueError('Debian-Paketlisten konnten nicht eingelesen werden.')
                temporary.replace(self.directory/'catalog.sqlite')
            except (OSError,ValueError,subprocess.SubprocessError):
                self.catalog_error = 'Debian-Paketlisten konnten nicht eingelesen werden. Vorhandener Katalog bleibt erhalten.'
            finally:
                temporary.unlink(missing_ok=True)
                self.catalog_guard.release()
        threading.Thread(target=build,daemon=True).start()
        return True

    def search(self, query, page):
        if not isinstance(query,str) or len(query)>120 or type(page) is not int or not 1<=page<=999999:
            raise ValueError('Ungültige Paketsuche.')
        tokens = re.findall(r'[a-z0-9+.-]+',query.lower())[:8]
        if len(query.strip())<2 or not tokens or not (self.directory/'catalog.sqlite').is_file():
            return dict(packages=[],pagination=dict(page=1,pages=1,total=0,page_size=10))
        with closing(sqlite3.connect('file:'+str(self.directory/'catalog.sqlite')+'?mode=ro',uri=True)) as db:
            db.row_factory = sqlite3.Row
            source, where, params = 'packages p','',[]
            if tokens:
                source = 'packages p JOIN search ON search.name=p.name'
                where = ' WHERE search MATCH ?'
                params = [' AND '.join('"'+token+'"*' for token in tokens)]
            total = db.execute('SELECT COUNT(*) FROM '+source+where,params).fetchone()[0]
            pages = max(1,(total+9)//10)
            page = min(page,pages)
            name = query.strip().lower()
            rows = db.execute('SELECT p.* FROM '+source+where+
                              ' ORDER BY (p.name=?) DESC, (substr(p.name,1,?)=?) DESC, p.name LIMIT 10 OFFSET ?',
                              [*params,name,len(name),name,(page-1)*10]).fetchall()
        with self.guard:
            found = [dict(row,installed={host:record['packages'].get(row['name'])
                     for host,record in self.inventory.items() if record.get('checked_at') and not record.get('error')},
                     installed_at={host:record.get('checked_at') for host,record in self.inventory.items()})
                     for row in rows]
        return dict(packages=found,
                    pagination=dict(page=page,pages=pages,total=total,page_size=10))

    def invalidate_inventory(self, hosts):
        with self.guard:
            for host in hosts:
                self.inventory.pop(host,None)
                self.inventory_epoch[host] = self.inventory_epoch.get(host,0)+1

    def inventory_state(self):
        with self.guard:
            return {host:{key:value for key,value in record.items() if key!='packages'}
                    for host,record in self.inventory.items()}

    def read_inventory(self, host):
        from dashboard.provision import ssh_command
        if host == TEACHER:
            output = self.teacher.inventory()
        else:
            pin = Path(self.enrolled()[host]['known_hosts'])
            command = ssh_command(host,pin,['/usr/bin/dpkg-query','-W','-f',
                '${binary:Package}\t${Architecture}\t${db:Status-Status}\t${Version}\n'])
            result = subprocess.run(command,capture_output=True,text=True,timeout=30)
            if result.returncode:
                raise ValueError('Client nicht erreichbar oder Paketstand konnte nicht gelesen werden.')
            output = result.stdout
        if len(output)>2097152:
            raise ValueError('Client nicht erreichbar oder Paketstand konnte nicht gelesen werden.')
        versions = {}
        for line in output.splitlines():
            fields = line.split('\t')
            if len(fields)!=4:
                raise ValueError('Client lieferte keinen gültigen Paketstand.')
            name,architecture,status,version = fields
            if status=='installed' and architecture in ('amd64','all'):
                name = name.split(':')[0]
                if re.fullmatch(r'[a-z0-9][a-z0-9+.-]{1,127}',name) and version:
                    versions[name] = version
        if not versions:
            raise ValueError('Kein installierter Paketstand gelesen.')
        return versions

    def refresh_inventory(self, hosts):
        hosts = self.targets(hosts)
        if not self.inventory_guard.acquire(blocking=False):
            return False
        with self.guard:
            epochs = {host:self.inventory_epoch.get(host,0) for host in hosts}
            for host in hosts:
                self.inventory[host] = dict(self.inventory.get(host,{}),refreshing=True)
        def read():
            try:
                with ThreadPoolExecutor(max_workers=SOFTWARE_WORKERS) as workers:
                    pending = {workers.submit(self.read_inventory,host):host for host in hosts}
                    for future in as_completed(pending):
                        host = pending[future]
                        try:
                            versions = future.result()
                            row = dict(packages=versions,count=len(versions),checked_at=datetime.now(BERLIN).isoformat(),error='',refreshing=False)
                        except (OSError,ValueError,KeyError,subprocess.SubprocessError):
                            row = dict(packages={},checked_at=None,error='Paketstand nicht erreichbar. Bitte erneut einlesen.',refreshing=False)
                        with self.guard:
                            if self.inventory_epoch.get(host,0)==epochs[host]:
                                self.inventory[host] = row
            finally:
                self.inventory_guard.release()
        try:
            threading.Thread(target=read,daemon=True).start()
        except BaseException:
            with self.guard:
                for host in hosts:
                    self.inventory[host]['refreshing'] = False
            self.inventory_guard.release()
            raise
        return True

    def validate_packages(self, names):
        names = packages(names)
        found={row['name'] for row in self.own.rows() if row['name'] in names}
        if len(found)==len(names):return names
        if not self.catalog()['count']:
            raise ValueError('Bitte zuerst den Debian-Katalog einlesen.')
        with closing(sqlite3.connect('file:'+str(self.directory/'catalog.sqlite')+'?mode=ro',uri=True)) as db:
            found.update(row[0] for row in db.execute('SELECT name FROM packages WHERE name IN ('+
                                                ','.join('?' for _ in names)+')',names))
        if found!=set(names):
            raise ValueError('Ein gewähltes Paket ist im Debian-Katalog nicht verfügbar.')
        return names

    def profiles(self):
        return sorted((self.profile(path.stem) for path in (self.directory/'profiles').glob('*.json')),
                      key=lambda row:row['name'].casefold())

    def profile(self, identity):
        row=read_settings(self.directory/'profiles'/(self.validate_id(identity)+'.json'))
        if row.get('approved')!=package_revision(row['packages'],self.own.snapshot(row['packages'])):
            row['approved']=None
        return row

    def save_profile(self, identity, name, selected, baseline):
        if (not isinstance(identity,str) or not isinstance(name,str) or not 1<=len(name.strip())<=80
                or any(ord(char)<32 for char in name) or type(baseline) is not bool):
            raise ValueError('Bitte einen Profilnamen mit höchstens 80 Zeichen angeben.')
        selected = self.validate_packages(selected)
        with self.guard:
            old = self.profile(identity) if identity else None
            if not old and len(self.profiles())>=100:
                raise ValueError('Höchstens 100 Softwareprofile speichern.')
            approved = old.get('approved') if old and old['packages']==selected else None
            if baseline and approved!=package_revision(selected,self.own.snapshot(selected)):
                raise ValueError('Software zunächst auf einem Pilotrechner installieren und vor Ort abnehmen.')
            row = dict(id=identity or uuid.uuid4().hex,name=name.strip(),packages=selected,baseline=baseline,
                       approved=approved,updated=self.now())
            profiles = [item for item in self.profiles() if item['id']!=row['id']] + [row]
            union = {package for item in profiles if item['baseline'] for package in item['packages']}
            if len(union)>50:
                raise ValueError('Die Baseline kann höchstens 50 zusätzliche Pakete enthalten.')
            self.atomic(self.directory/'profiles'/(row['id']+'.json'),row)
            return row

    def delete_profile(self, identity):
        with self.guard:
            self.profile(identity)
            (self.directory/'profiles'/(identity+'.json')).unlink()

    def export(self, identity, *, profile=None, debs=None):
        profile = profile or self.profile(identity)
        source = client_source()
        debs=self.own.snapshot(profile['packages']) if debs is None else debs
        play = dict(name='CISCO-LAB Softwareprofil: '+profile['name'],hosts='cisco_lab',become=True,gather_facts=False,
                    vars=dict(lab_software_packages=packages(profile['packages']),lab_software_debs=debs),tasks=[
                        dict(name='Genau einen LAB-Client auswählen',**{'ansible.builtin.assert':dict(
                            that=["ansible_play_hosts_all | length == 1","inventory_hostname is match('^LAB-0[1-8][AB]$')"])}),
                        dict(name='Softwareprofil installieren und Desktopstatus anzeigen',**{
                            'ansible.builtin.command':dict(argv=['/usr/bin/python3','-B','-c',source,'install'],
                                stdin="{{ {'packages': lab_software_packages, 'host': inventory_hostname, 'debs': lab_software_debs} | to_json }}"),
                            'register':'lab_software_result',
                            'changed_when':"(lab_software_result.stdout | from_json).changed | default(false)"})])
        if debs:
            play['tasks'][1:1]=[
                dict(name='LAB-Paketcache anlegen',**{'ansible.builtin.file':dict(path='/var/cache/cisco-lab-software',state='directory',owner='root',group='root',mode='0755')}),
                dict(name='Eigene Paketdateien bereitstellen',**{'ansible.builtin.copy':dict(
                    src='{{ playbook_dir }}/packages/{{ item.sha256 }}.deb',dest='/var/cache/cisco-lab-software/{{ item.sha256 }}.deb',owner='root',group='root',mode='0644'),
                    'loop':'{{ lab_software_debs }}'})]
        return yaml.safe_dump([play],allow_unicode=True,sort_keys=False).encode()

    def write_export_zip(self,identity,output):
        import zipfile
        profile=self.profile(identity)
        rows=self.own.snapshot(profile['packages'])
        with zipfile.ZipFile(output,'w',compression=zipfile.ZIP_STORED) as archive:
            archive.writestr('softwareprofil.yml',self.export(identity,profile=profile,debs=rows))
            for row in rows:
                archive.write(self.own.path(row),'packages/'+row['sha256']+'.deb')

    @staticmethod
    def enrolled():
        from maintenance.controller import targets
        return targets()

    def targets(self, selected, *, students_only=False):
        if (not isinstance(selected,list) or not 1<=len(selected)<=17
                or any(not isinstance(host,str) or host not in {*LAB_TARGETS, TEACHER} for host in selected)
                or len(set(selected))!=len(selected)):
            raise ValueError('Bitte bekannte LAB-Rechner auswählen.')
        enrolled = self.enrolled()
        if not students_only and self.teacher.state()['ready']:
            enrolled = dict(enrolled, **{TEACHER: {}})
        if any(host not in enrolled for host in selected):
            raise ValueError('Softwareinstallation erst nach vollständiger APP-Aufnahme möglich.')
        if any(reserved(host) for host in selected):
            raise ValueError('Ein ausgewählter Client ist gerade für die Abendwartung reserviert.')
        return sorted(selected)

    def save(self, run):
        self.atomic(self.directory/'runs'/(run['id']+'.json'),run)

    def record(self, identity):
        return self.read_run(self.directory/'runs'/(self.validate_id(identity)+'.json'))

    @staticmethod
    def read_run(path):
        # Per-client dependency plans are larger than a hardware manifest.
        return read_settings(path, max_bytes=MAX_RUN_BYTES, label='Software-Auftragsdatei')

    def records(self):
        return sorted((self.read_run(path) for path in (self.directory/'runs').glob('*.json')),
                      key=lambda row:(row['started'],row['id']),reverse=True)

    def state(self):
        rows = self.records()
        teacher = self.teacher.state()
        own=self.own.rows()
        with self.guard:
            checked={host:row for host,row in self.inventory.items() if row.get('checked_at') and not row.get('error')}
            for package in own:
                package['installed']={host:row['packages'].get(package['name']) for host,row in checked.items()}
                package['installed_at']={host:row['checked_at'] for host,row in checked.items()}
        return dict(catalog=self.catalog(),profiles=self.profiles(),own_packages=own,hosts=sorted(self.enrolled())+([TEACHER] if teacher['ready'] else []),
                    teacher=teacher,
                    inventory=self.inventory_state(),inventory_refreshing=self.inventory_guard.locked(),
                    busy=self.deployments.lock.locked(), parallelism=SOFTWARE_WORKERS,
                    runs=[{key:value for key,value in row.items() if key!='plans'} for row in rows])

    @staticmethod
    def revision():
        return hashlib.sha256((ROOT/'software-install.yml').read_bytes()+
                              (ROOT/'tasks/software-install.yml').read_bytes()+
                              (ROOT/'dashboard/software_client.py').read_bytes()+
                              (ROOT/'dashboard/deb_client.py').read_bytes()).hexdigest()

    def create_plan(self, selected, hosts, profile_id=''):
        if not isinstance(profile_id,str):
            raise ValueError('Ungültiges Softwareprofil.')
        selected = self.validate_packages(selected)
        hosts = self.targets(hosts)
        if profile_id and self.profile(profile_id)['packages']!=selected:
            raise ValueError('Profil und ausgewählte Pakete stimmen nicht überein.')
        return self.launch(dict(kind='plan',packages=selected,targets=hosts,profile_id=profile_id,
                                debs=self.own.snapshot(selected),revision=self.revision()))

    def start(self, run_id, confirmed):
        if confirmed is not True:
            raise ValueError('Installation ausdrücklich bestätigen.')
        plan = self.record(run_id)
        if (plan['kind']!='plan' or plan['status']!='succeeded' or plan.get('consumed')
                or datetime.fromisoformat(plan['finished'])<datetime.now(BERLIN)-timedelta(minutes=15)
                or plan['revision']!=self.revision()):
            raise ValueError('Bitte die Installation erneut prüfen; die Vorschau ist nicht mehr gültig.')
        self.targets(plan['targets'])
        if self.own.snapshot(plan['packages'])!=plan.get('debs',[]):
            raise ValueError('Eigenes Paket seit der Vorschau aktualisiert. Bitte Installation erneut prüfen.')
        if any(row.get('removals') for row in plan['plans'].values()):
            raise ValueError('Die Auswahl würde Pakete entfernen. Bitte Auswahl anpassen.')
        return self.launch(dict(kind='install',packages=plan['packages'],targets=plan['targets'],
                                profile_id=plan.get('profile_id',''),plans=plan['plans'],
                                debs=plan.get('debs',[]),
                                revision=plan['revision'],plan_id=run_id))

    def launch(self, fields):
        if not self.deployments.lock.acquire(blocking=False):
            return None
        try:
            if fields.get('plan_id'):
                prior = self.record(fields['plan_id'])
                if prior.get('consumed'):
                    raise ValueError('Diese Vorschau wurde bereits verwendet. Bitte erneut prüfen.')
                prior['consumed'] = True
                self.save(prior)
            run = dict(fields,id=uuid.uuid4().hex,started=self.now(),status='running',results={},active=[])
            if run['kind']=='install':
                self.invalidate_inventory(run['targets'])
            if run['kind']=='plan':
                run['plans'] = {}
            self.save(run)
            threading.Thread(target=self.execute,args=(run,),daemon=True).start()
            return {key:value for key,value in run.items() if key!='plans'}
        except BaseException:
            self.deployments.lock.release()
            raise

    def on_host(self, host, run):
        if run['kind']=='plan' and run.get('debs'):self.ensure_debs(host,run['debs'])
        if host == TEACHER:
            return self.on_teacher(run)
        pin = Path(self.enrolled()[host]['known_hosts'])
        if run['kind']=='plan':
            from dashboard.provision import ssh_command
            command = ssh_command(host,pin,['sudo','-n','/usr/bin/python3','-B','-c',
                                            client_source(),'plan'])
            payload=dict(host=host,packages=run['packages'])
            if run.get('debs'):payload['debs']=run['debs']
            result = subprocess.run(command,input=json.dumps(payload),
                                    capture_output=True,text=True,timeout=90)
            try:
                data = json.loads(result.stdout)
            except ValueError:
                raise ValueError('Client nicht erreichbar oder Paketprüfung fehlgeschlagen. APP-Zugang prüfen.') from None
            if result.returncode or 'error' in data:
                raise ValueError(data.get('error','Paketprüfung fehlgeschlagen.'))
            if data.get('host')!=host or data.get('packages')!=run['packages'] or not re.fullmatch('[a-f0-9]{64}',data.get('digest','')):
                raise ValueError('Paketprüfung lieferte keine gültige Vorschau.')
            return data
        from dashboard.provision import ssh_command
        command = ssh_command(host,pin,['sudo','-n','/usr/bin/python3','-B','-c',
                                        client_source(),'install'])
        path = self.directory/'runs'/(run['id']+'-'+host+'.log')
        with path.open('w',buffering=1) as output:
            os.chmod(path,0o600)
            output.write(host+' · Paketinstallation läuft auf dem Client.\n')
            payload=dict(host=host,packages=run['packages'],expected=run['plans'][host]['digest'])
            if run.get('debs'):payload['debs']=run['debs']
            result = subprocess.run(command,input=json.dumps(payload),stdout=subprocess.PIPE,stderr=output,
                                    text=True,timeout=3600)
            output.write(result.stdout[-65536:])
        try:
            data = json.loads(result.stdout)
        except ValueError:
            raise ValueError('Client nicht erreichbar oder Installation fehlgeschlagen. Protokoll öffnen.') from None
        if result.returncode or 'error' in data:
            raise ValueError(data.get('error','Installation fehlgeschlagen. Protokoll öffnen.'))
        if data.get('installed') != run['packages'] or type(data.get('changed')) is not bool:
            raise ValueError('Client lieferte keine gültige Installationsbestätigung.')
        return data

    def on_teacher(self, run):
        options={'debs':run['debs']} if run.get('debs') else {}
        if run['kind'] == 'plan':
            data = self.teacher.decode(self.teacher.execute('plan', run['packages'],**options))
            if (data.get('host') != TEACHER or data.get('packages') != run['packages']
                    or not re.fullmatch('[a-f0-9]{64}', data.get('digest', ''))):
                raise ValueError('Teacher lieferte keine gültige Paketvorschau.')
            return data
        path = self.directory/'runs'/(run['id']+'-'+TEACHER+'.log')
        with path.open('w', buffering=1) as output:
            os.chmod(path, 0o600)
            output.write(TEACHER+' · Softwareprofil wird installiert.\n')
            result = self.teacher.execute('install', run['packages'], run['plans'][TEACHER]['digest'], stderr=output,**options)
            output.write(result.stdout[-65536:])
        data = self.teacher.decode(result)
        if data.get('installed') != run['packages'] or type(data.get('changed')) is not bool:
            raise ValueError('Teacher lieferte keine gültige Installationsbestätigung.')
        return data

    def ensure_debs(self,host,rows):
        from dashboard.provision import ssh_command
        if host==TEACHER:
            answer=self.teacher.decode(self.teacher.execute('cached',debs=rows))
        else:
            pin=Path(self.enrolled()[host]['known_hosts'])
            command=ssh_command(host,pin,['sudo','-n','/usr/bin/python3','-B','-c',client_source(),'cached'])
            result=subprocess.run(command,input=json.dumps(dict(host=host,debs=rows)),capture_output=True,text=True,timeout=30)
            if result.returncode:raise ValueError('Client-Paketcache konnte nicht geprüft werden.')
            answer=json.loads(result.stdout)
        if not isinstance(answer,dict) or not isinstance(answer.get('cached'),list):
            raise ValueError('Ungültige LAB-Paketcache-Antwort.')
        for row in rows:
            if row['sha256'] in answer['cached']:continue
            path=self.own.path(row)
            if host==TEACHER:
                self.teacher.upload(row,path)
            else:
                command=ssh_command(host,pin,['sudo','-n','/usr/bin/python3','-B','-c',client_source(),
                                            'receive',host,row['sha256'],str(row['size'])])
                with path.open('rb') as package:
                    result=subprocess.run(command,stdin=package,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=600)
                if result.returncode or json.loads(result.stdout)!=dict(received=row['sha256'],size=row['size']):
                    raise ValueError('Eigene Paketdatei wurde vom Client nicht bestätigt.')

    @staticmethod
    def on_host_command(host, pin, selected, expected='', debs=None):
        if host not in LAB_TARGETS or not pin.is_file():
            raise ValueError('Bekanntes LAB-Ziel und verifizierten Hostschlüssel verlangen.')
        settings = dict(ansible_host=LAB_TARGETS[host],ansible_user='administrator',ansible_ssh_use_tty=False,
                        ansible_pipelining=True,ansible_ssh_args='-o ControlMaster=no -o ControlPath=none',
                        ansible_ssh_common_args='-F /dev/null -o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes '
                        '-o ConnectTimeout=8 -o ServerAliveInterval=15 -o ServerAliveCountMax=3 '
                        '-o GlobalKnownHostsFile=/dev/null -o UserKnownHostsFile='+shlex.quote(str(pin)),
                        lab_software_packages=packages(selected),lab_software_expected=expected,
                        lab_software_debs=debs or OwnPackages().snapshot(selected),
                        lab_software_deb_directory=str(OwnPackages().directory))
        return [ansible_tool('ansible-playbook'),'-i','inventory.ini','--limit',host,'--private-key',
                   str(Path.home()/'.ssh/id_ed25519'),'-e',json.dumps(settings),'software-install.yml']

    def execute(self, run):
        def on_host(host):
            with self.guard:
                run['active'].append(host)
                self.save(run)
            return self.on_host(host,run)
        try:
            run.setdefault('active', [])
            with ThreadPoolExecutor(max_workers=SOFTWARE_WORKERS) as workers:
                pending = {workers.submit(on_host,host):host for host in run['targets']}
                for future in as_completed(pending):
                    host = pending[future]
                    try:
                        result = future.result()
                        with self.guard:
                            if run['kind']=='plan':
                                run['plans'][host] = result
                                result = {key:value for key,value in result.items() if key!='digest'}
                            run['results'][host] = dict(status='succeeded',**result)
                    except (OSError,ValueError,KeyError,subprocess.SubprocessError) as error:
                        with self.guard:
                            run['results'][host] = dict(status='failed',error=str(error))
                    with self.guard:
                        run['active'].remove(host)
                        self.save(run)
            run['status'] = 'succeeded' if all(row['status']=='succeeded' for row in run['results'].values()) else 'failed'
        finally:
            try:
                if run['kind']=='install':
                    self.invalidate_inventory(run['targets'])
                run.update(finished=self.now())
                if run['status']=='running':
                    run['status']='failed'
                self.save(run)
            finally:
                self.deployments.lock.release()

    def review(self, run_id, confirmed):
        if confirmed is not True:
            raise ValueError('Programmtest vor Ort bestätigen.')
        run = self.record(run_id)
        already_present=(run['kind']=='plan' and len(run['results'])==len(run['targets']) and bool(run['targets'])
                         and all(row.get('status')=='succeeded' and not row.get('changes') for row in run['results'].values()))
        if ((run['kind']!='install' and not already_present) or run['status']!='succeeded' or not run.get('profile_id')
                or not any(host in ('LAB-08A','LAB-08B') for host in run['targets'])
                or run['revision']!=self.revision()):
            raise ValueError('Ein erfolgreich auf einem Pilotrechner installiertes Profil abnehmen.')
        with self.guard:
            profile = self.profile(run['profile_id'])
            if profile['packages']!=run['packages'] or self.own.snapshot(profile['packages'])!=run.get('debs',[]):
                raise ValueError('Profil seit der Installation geändert. Bitte erneut testen.')
            profile.update(approved=package_revision(profile['packages'],run.get('debs')),reviewed_at=self.now())
            self.atomic(self.directory/'profiles'/(profile['id']+'.json'),profile)
        return profile

    def output(self, identity, host):
        if host not in {*LAB_TARGETS, TEACHER}:
            raise ValueError('Unbekannter LAB-Client.')
        run = self.record(identity)
        if host not in run['targets']:
            raise ValueError('Client gehört nicht zu diesem Auftrag.')
        path = self.directory/'runs'/(identity+'-'+host+'.log')
        with path.open('rb') as output:
            output.seek(max(0,path.stat().st_size-65536))
            return output.read().decode(errors='replace')
