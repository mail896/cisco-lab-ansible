"""Provision the explicitly approved local shadow of the LAB account."""
from datetime import date, datetime, timezone
import fcntl
import grp
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import stat
import subprocess
import sys
import tempfile

NAME = 'cisco'
HOME = Path('/var/lib/cisco-lab-local/cisco')
MARKER = Path('/etc/cisco-lab/local-cisco.json')
PAM = Path('/etc/pam.d')
LOCAL_SESSION = PAM/'cisco-lab-local-session'
SSH = Path('/etc/ssh/sshd_config.d/04-cisco-lab-local.conf')
PASSWD = Path('/etc/passwd')
SHADOW = Path('/etc/shadow')
NSS = Path('/etc/nsswitch.conf')
LIBRARY = Path('/usr/lib/x86_64-linux-gnu/libnss_lab_cisco.so.2')
INITGROUPS = 'initgroups:     lab_cisco [SUCCESS=return] files [SUCCESS=continue] systemd [SUCCESS=continue] winbind'

SESSION_RULES = '''session [success=1 default=ignore] pam_succeed_if.so quiet user = cisco
session substack common-session
session [success=ignore default=1] pam_succeed_if.so quiet user = cisco
session substack cisco-lab-local-session'''
AUTH_RULES = '''auth [success=ignore default=1] pam_succeed_if.so quiet user = cisco
auth [success=done default=die] pam_unix.so
@include common-auth'''


def read(*command):
    return subprocess.check_output(command,text=True,timeout=15).strip()


def safe_write(path, data, mode=0o644, gid=0):
    if path.is_symlink():
        raise RuntimeError(f'Unerwarteter Symlink: {path}')
    if path.exists():
        current = path.stat()
        if not stat.S_ISREG(current.st_mode) or current.st_uid != 0:
            raise RuntimeError(f'Unerwartete Datei: {path}')
        mode, gid = stat.S_IMODE(current.st_mode), current.st_gid
    with tempfile.NamedTemporaryFile(mode='wb',dir=path.parent,delete=False) as file:
        name = Path(file.name)
        try:
            file.write(data.encode() if isinstance(data,str) else data)
            file.flush()
            os.fchmod(file.fileno(),mode)
            os.fchown(file.fileno(),0,gid)
            os.fsync(file.fileno())
            name.replace(path)
        finally:
            name.unlink(missing_ok=True)


def pam_config(file, normal_auth):
    text=file.read_text()
    if SESSION_RULES not in text:
        line=re.compile(r'^@include[ \t]+common-session[ \t]*$',re.M)
        if len(line.findall(text)) != 1:
            raise RuntimeError(f'Unbekannter PAM-Sitzungsaufbau: {file}')
        text=line.sub(SESSION_RULES,text)
    if normal_auth and AUTH_RULES not in text:
        line=re.compile(r'^@include[ \t]+common-auth[ \t]*$',re.M)
        if len(line.findall(text)) != 1:
            raise RuntimeError(f'Unbekannter PAM-Anmeldeaufbau: {file}')
        text=line.sub(AUTH_RULES,text)
    return text


def local_session():
    removed = {'pam_winbind.so','pam_mount.so','pam_script.so','pam_krb5.so'}
    lines=(PAM/'common-session').read_text().splitlines()
    for line in lines:
        if any(name in line.split() for name in removed) and not line.lstrip().startswith('#'):
            if not re.match(r'\s*session\s+optional\s+',line):
                raise RuntimeError('Unbekannte IServ-PAM-Regel, lokale Sitzung nicht erzeugt.')
    filtered=[line for line in lines if not removed.intersection(line.split())]
    if not any('pam_systemd.so' in line.split() for line in filtered):
        raise RuntimeError('pam_systemd fehlt im lokalen Sitzungsaufbau.')
    return '\n'.join(filtered)+'\n'


def local_record(path):
    matches=[line.split(':') for line in path.read_text().splitlines() if line.startswith(NAME+':')]
    if len(matches)>1:
        raise RuntimeError(f'Doppelter Eintrag in {path}.')
    return matches[0] if matches else None


def put_record(text, fields):
    wanted=':'.join(str(value) for value in fields)
    lines=text.splitlines()
    for index,line in enumerate(lines):
        if line.startswith(NAME+':'):
            lines[index]=wanted
            break
    else:
        lines.append(wanted)
    return '\n'.join(lines)+'\n'


def account(password_hash, group):
    uid=None
    with Path('/etc/.pwd.lock').open('a+') as lock:
        fcntl.lockf(lock,fcntl.LOCK_EX)
        before_passwd=PASSWD.read_text()
        before_shadow=SHADOW.read_text()
        current=local_record(PASSWD)
        old_shadow=local_record(SHADOW)
        if current:
            if not MARKER.is_file() or MARKER.is_symlink() or not old_shadow:
                raise RuntimeError('Vorhandenes lokales cisco-Konto ist nicht von diesem Playbook verwaltet.')
            marker=json.loads(MARKER.read_text())
            uid=int(current[2])
            if (marker != {'uid':uid,'gid':group} or not 400 <= uid < 500
                    or int(current[3]) != group or current[5] != str(HOME)
                    or current[6] != '/bin/bash'):
                raise RuntimeError('Vorhandenes lokales cisco-Konto hat unerwartete Eigenschaften.')
        else:
            if old_shadow or MARKER.exists() or MARKER.is_symlink():
                raise RuntimeError('Unvollständiger lokaler Kontostand. Bitte prüfen.')
            occupied={entry.pw_uid for entry in pwd.getpwall()}
            uid=next((number for number in range(498,399,-1) if number not in occupied),None)
            if uid is None:
                raise RuntimeError('Keine freie lokale UID unter 500.')
        if HOME.is_symlink() or (HOME.exists() and (not HOME.is_dir() or HOME.stat().st_uid != uid)):
            raise RuntimeError('Unerwartetes lokales Home.')
        if not HOME.exists():
            HOME.parent.mkdir(mode=0o755,parents=True,exist_ok=True)
            shutil.copytree('/etc/skel',HOME,symlinks=True)
            for path in (HOME,*HOME.rglob('*')):
                os.chown(path,uid,group,follow_symlinks=False)
            HOME.chmod(0o700)
        shadow=[NAME,'!',(date.today()-date(1970,1,1)).days,0,99999,7,'','','']
        try:
            if not current:
                safe_write(PASSWD,put_record(before_passwd,
                           [NAME,'x',uid,group,'CISCO-LAB Sammelkonto',HOME,'/bin/bash']))
            entry=pwd.getpwnam(NAME)
            assigned = {int(value) for value in read('id','-G',NAME).split()}
            if entry.pw_uid!=uid or entry.pw_gid!=group or assigned!={group}:
                raise RuntimeError('NSS löst cisco nicht ausschließlich mit den erwarteten lokalen Rechten auf.')
            if current and old_shadow[1] == password_hash:
                return
            shadow[1]=password_hash
            safe_write(SHADOW,put_record(before_shadow,shadow),0o640)
            MARKER.parent.mkdir(mode=0o755,parents=True,exist_ok=True)
            safe_write(MARKER,json.dumps({'uid':uid,'gid':group})+'\n')
        except BaseException:
            safe_write(SHADOW,before_shadow,0o640)
            safe_write(PASSWD,before_passwd)
            raise


def main():
    if os.geteuid()!=0:
        raise SystemExit('Nur als lokale Administration ausführen.')
    payload=json.load(sys.stdin)
    if payload.get('allow_shadow') is not True:
        raise ValueError('Überdeckung des IServ-Kontos ausdrücklich bestätigen.')
    password_hash=payload.get('hash','')
    if not re.fullmatch(r'\$6\$[A-Za-z0-9./]{8,16}\$[A-Za-z0-9./]{86}',password_hash):
        raise ValueError('Gültiger lokaler Passwort-Hash erforderlich.')
    if not re.search(r'^passwd:\s+files(?:\s|$)',Path('/etc/nsswitch.conf').read_text(),re.M):
        raise RuntimeError('Lokale Konten haben in NSS keine bestätigte Priorität.')
    group=grp.getgrnam('cisco.lab').gr_gid
    if not 500 <= group <= 1999999:
        raise RuntimeError('LAB-GID liegt außerhalb des bestätigten Bereichs.')
    if local_record(PASSWD) is not None and not MARKER.is_file():
        raise RuntimeError('Ein anderes lokales cisco-Konto ist bereits vorhanden.')
    nss=NSS.read_text()
    if INITGROUPS not in nss:
        if re.search(r'^initgroups:',nss,re.M) or not re.search(
            r'^group:\s+files\s+systemd\s+winbind\s*$',nss,re.M):
            raise RuntimeError('Unbekannte NSS-Gruppenreihenfolge.')
        nss=nss.rstrip()+'\n'+INITGROUPS+'\n'
    library=Path(payload.get('library',''))
    if not library.is_absolute() or not library.is_file() or library.is_symlink():
        raise RuntimeError('Geprüftes NSS-Modul fehlt.')
    groups_before={user:read('id','-G',user) for user in ('administrator','s.test')}
    changes={LIBRARY:library.read_bytes(),NSS:nss,
             PAM/'sddm':pam_config(PAM/'sddm',True),
             PAM/'sddm-autologin':pam_config(PAM/'sddm-autologin',False),
             LOCAL_SESSION:local_session(),SSH:'DenyUsers cisco\n'}
    old={path:(path.read_bytes() if isinstance(value,bytes) else path.read_text()) if path.is_file() else None
         for path,value in changes.items()}
    if old != changes:
        backup=Path('/var/backups/cisco-lab/local-cisco')/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
        backup.mkdir(mode=0o700,parents=True)
        for path,value in old.items():
            if value is not None:
                saved=backup/path.name
                saved.write_bytes(value.encode() if isinstance(value,str) else value)
                saved.chmod(0o600)
    try:
        for path,value in changes.items():
            if old[path]!=value:
                safe_write(path,value)
        if any(read('id','-G',user)!=groups for user,groups in groups_before.items()):
            raise RuntimeError('Gruppenauflösung für ein bestehendes Konto hat sich geändert.')
        read('/usr/sbin/sshd','-t')
        if 'denyusers cisco' not in read('/usr/sbin/sshd','-T').splitlines():
            raise RuntimeError('SSH-Ausschluss für das Sammelkonto ist nicht wirksam.')
        if changes[SSH] != old[SSH]:
            read('systemctl','reload','ssh.service')
        account(password_hash,group)
    except BaseException:
        for path,value in old.items():
            if value is None:
                path.unlink(missing_ok=True)
            elif changes[path]!=value:
                safe_write(path,value)
        subprocess.run(['systemctl','reload','ssh.service'],check=False,timeout=15)
        raise


if __name__=='__main__':
    main()
