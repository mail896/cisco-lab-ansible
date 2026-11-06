"""Read-only playbook catalogue, single-client admission, and maintenance pilot."""
from datetime import datetime
import json
from pathlib import Path
import re
import shlex
import subprocess
import threading
import uuid

from dashboard.deploy import JOBS, LAB_TARGETS, ROOT
from maintenance.preview import BERLIN, start_allowed

ENTRIES = {key:{'label':label,'source':entry,'kind':'pilot'} for key,(label,_,entry) in JOBS.items()}
ENTRIES.update(
    bootstrap={'label':'SSH-Erstaufnahme','source':'bootstrap-client.yml','kind':'local'},
    fresh={'label':'Basisinstallation für neues Image','source':'fresh-client.yml','kind':'fresh'},
    maintenance={'label':'APT-Wartungspilot','source':'maintenance/pilot.py','kind':'maintenance'},
)
ADMISSIONS = ROOT/'local-private/fresh-clients'


def source(entry):
    if entry not in ENTRIES:
        raise FileNotFoundError
    return (ROOT/ENTRIES[entry]['source']).read_text()


def ssh_command(host, known_hosts_b, command):
    known = known_hosts_b if host=='LAB-08B' else Path.home()/'.ssh/known_hosts'
    if host not in LAB_TARGETS or not known or not known.is_file():
        raise ValueError('Verifizierter SSH-Hostschlüssel fehlt.')
    return ['/usr/bin/ssh','-T','-F','/dev/null','-o','BatchMode=yes','-o','IdentitiesOnly=yes',
            '-o','StrictHostKeyChecking=yes','-o','GlobalKnownHostsFile=/dev/null',
            '-o',f'UserKnownHostsFile={known}','-o','ConnectTimeout=5',
            '-i',str(Path.home()/'.ssh/id_ed25519'),
            f'administrator@{LAB_TARGETS[host]}',shlex.join(command)]


class Provision:
    def __init__(self, directory, deployments):
        self.directory = directory
        self.deployments = deployments
        directory.mkdir(mode=0o700,parents=True,exist_ok=True)
        for item in self.history():
            if item['status']=='running':
                item['status']='interrupted'
                self.save(item)

    def save(self, run):
        path = self.directory/(run['id']+'.json')
        tmp = path.with_suffix('.tmp')
        tmp.write_text(json.dumps(run,ensure_ascii=False))
        tmp.replace(path)

    def history(self):
        return sorted((json.loads(path.read_text()) for path in self.directory.glob('*.json')),
                      key=lambda run:run['started'],reverse=True)[:15]

    @staticmethod
    def admission(host):
        if host not in LAB_TARGETS:
            raise ValueError('Bitte genau einen LAB-Client wählen.')
        path = ADMISSIONS/(host+'.json')
        if path.is_symlink() or not path.is_file() or path.stat().st_mode & 0o077:
            raise ValueError('Keine private Erstaufnahme-Datei für diesen Client.')
        data = json.loads(path.read_text())
        manifest = data.get('lab_network_manifest',{})
        known = Path(data.get('lab_fresh_known_hosts','')).expanduser()
        if (data.get('lab_fresh_host')!=host or manifest.get('host')!=host
                or data.get('lab_fresh_confirmed') is not True
                or not known.is_absolute() or not known.is_file()
                or not re.fullmatch(r'([0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}',
                                    manifest.get('identity',{}).get('school',{}).get('mac',''))):
            raise ValueError('Host, Hardware oder verifizierte SSH-Hostdatei stimmen nicht.')
        return path

    def fresh_state(self):
        ready=[]
        for host in LAB_TARGETS:
            try:
                self.admission(host)
            except (OSError,ValueError):
                continue
            ready.append(host)
        return dict(hosts=ready,runs=self.history())

    def fresh_start(self, host, confirmed):
        if confirmed is not True:
            raise ValueError('Basisinstallation ausdrücklich bestätigen.')
        if host=='LAB-08B' and start_allowed(datetime.now(BERLIN)):
            raise ValueError('LAB-08B ist für die Wartung reserviert.')
        if not self.deployments.lock.acquire(blocking=False):
            return None
        try:
            admission=self.admission(host)
            run=dict(id=uuid.uuid4().hex,host=host,started=datetime.now(BERLIN).isoformat(timespec='seconds'),
                     status='running')
            self.save(run)
            threading.Thread(target=self.install,args=(run,admission),daemon=True).start()
            return dict(run)
        except BaseException:
            self.deployments.lock.release()
            raise

    def install(self, run, admission):
        used = admission.with_suffix('.'+run['id']+'.used')
        try:
            admission.replace(used)
            host=run['host']
            settings={'lab_fresh_install_veyon':False,'ansible_host':LAB_TARGETS[host],
                      'ansible_user':'administrator','ansible_ssh_args':'-o ControlMaster=no -o ControlPath=none'}
            images=self.deployments.appearance.active()
            settings.update(lab_greeter_image_source=str(images['lock'][1]),
                            lab_desktop_image_source=str(images['desktop'][1]))
            command=['/usr/bin/ansible-playbook','-i','inventory.ini','--limit',host,'--private-key',
                     str(Path.home()/'.ssh/id_ed25519'),'-e','@'+str(used),'-e',json.dumps(settings),'fresh-client.yml']
            with (self.directory/(run['id']+'.log')).open('w') as log:
                result=subprocess.run(command,cwd=ROOT,stdin=subprocess.DEVNULL,
                                      stdout=log,stderr=subprocess.STDOUT,timeout=1500)
                if result.returncode==0:
                    log.write('\nBasis abgeschlossen. Lokales Sammelkonto wird eingerichtet.\n')
                    log.flush()
                    known=Path(json.loads(used.read_text())['lab_fresh_known_hosts']).expanduser()
                    cisco=self.deployments.command(host,'cisco',release=True)
                    secure=('-F /dev/null -o BatchMode=yes -o IdentitiesOnly=yes '
                            '-o StrictHostKeyChecking=yes -o ConnectTimeout=8 '
                            f'-o UserKnownHostsFile={shlex.quote(str(known))} -o GlobalKnownHostsFile=/dev/null')
                    cisco[-2]=json.dumps(dict(json.loads(cisco[-2]),ansible_ssh_common_args=secure))
                    result=subprocess.run(cisco,cwd=ROOT,stdin=subprocess.DEVNULL,
                                          stdout=log,stderr=subprocess.STDOUT,timeout=300)
                    if result.returncode==0:
                        log.write('\nInstallation abgeschlossen. Nach dem Neustart IServ-Erstlogin, '
                                  'Files und lokale cisco-Anmeldung mit LAB-Desktopbild prüfen. '
                                  'Der versionsgebundene Winbind-Testpatch ist ein separater Schritt.\n')
            run['status']='succeeded' if result.returncode==0 else 'failed'
        except (OSError,ValueError,subprocess.SubprocessError):
            run['status']='failed'
        finally:
            try:
                run['finished']=datetime.now(BERLIN).isoformat(timespec='seconds')
                self.save(run)
            finally:
                self.deployments.lock.release()

    def output(self, run_id):
        self.deployments.validate_id(run_id)
        path=self.directory/(run_id+'.log')
        with path.open('rb') as file:
            file.seek(max(0,path.stat().st_size-65536))
            return file.read().decode(errors='replace')

    def maintenance_state(self):
        host='LAB-08B'
        script=('printf "%s\\n" "$(systemctl show -p ActiveState --value cisco-lab-maintenance.service)"; '
                'systemctl list-timers cisco-lab-maintenance.timer --no-pager; '
                'sudo -n journalctl -u cisco-lab-maintenance.service -n 30 --no-pager')
        result=subprocess.run(ssh_command(host,self.deployments.known_hosts_b,['sh','-c',script]),
                              text=True,capture_output=True,timeout=15)
        if result.returncode:
            return {'available':False,'can_start':False,'output':'LAB-08B oder Wartungsprotokoll nicht erreichbar.'}
        lines=result.stdout.splitlines()
        return {'available':True,'active':lines[0] if lines else 'unknown',
                'can_start':start_allowed(datetime.now(BERLIN)) and bool(lines)
                             and lines[0] in ('inactive','failed'),
                'output':'\n'.join(lines[1:])}

    def maintenance_start(self, confirmed):
        if confirmed is not True or not start_allowed(datetime.now(BERLIN)):
            raise ValueError('Nur nach Bestätigung im Wartungsfenster 18–20 Uhr.')
        if not self.deployments.lock.acquire(blocking=False):
            return None
        try:
            cmd=ssh_command('LAB-08B',self.deployments.known_hosts_b,
                            ['sudo','-n','systemctl','start','--no-block','cisco-lab-maintenance.service'])
            subprocess.run(cmd,text=True,capture_output=True,check=True,timeout=12)
            return {'started':True}
        finally:
            self.deployments.lock.release()
