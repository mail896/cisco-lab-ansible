"""Fixed client-side package planner/installer and per-user progress window."""
import hashlib
import json
import os
from pathlib import Path
import platform
import pwd
import re
import socket
import stat
import subprocess
import sys
import time
import uuid

PACKAGE = re.compile(r'[a-z0-9][a-z0-9+.-]{1,127}\Z')
GUI = r'''
import json,sys,time
from pathlib import Path
from PyQt5.QtCore import QTimer,Qt
from PyQt5.QtGui import QIcon
from PyQt5.QtWidgets import QApplication,QDialog,QVBoxLayout,QLabel,QProgressBar,QPlainTextEdit,QPushButton
app=QApplication(sys.argv)
window=QDialog();window.setWindowTitle('CISCO-LAB · Softwareinstallation')
window.setWindowIcon(QIcon.fromTheme('system-software-install'));window.resize(560,280)
layout=QVBoxLayout(window)
title=QLabel('Software wird installiert');title.setStyleSheet('font-size:18px;font-weight:600;color:#234a37')
title.setTextFormat(Qt.PlainText);layout.addWidget(title)
packages=QLabel();packages.setTextFormat(Qt.PlainText);packages.setWordWrap(True);layout.addWidget(packages)
bar=QProgressBar();bar.setRange(0,0);layout.addWidget(bar)
details=QPlainTextEdit();details.setReadOnly(True);details.setMaximumBlockCount(8);layout.addWidget(details)
close=QPushButton('Schließen');close.clicked.connect(window.close);close.hide();layout.addWidget(close)
identity=sys.argv[2];completed=None
def update():
 global completed
 try:
  data=json.loads(Path(sys.argv[1]).read_text())
  if data['id']!=identity:window.close();return
  packages.setText(', '.join(data['packages']))
  details.setPlainText(data.get('message',''))
  if data['status']=='succeeded':
   title.setText('Softwareinstallation abgeschlossen');bar.setRange(0,100);bar.setValue(100)
   if completed is None:completed=time.monotonic()
   if time.monotonic()-completed>=5:window.close()
  elif data['status']=='failed':
   title.setText('Softwareinstallation fehlgeschlagen');bar.hide();close.show()
 except (OSError,ValueError,KeyError):pass
timer=QTimer();timer.timeout.connect(update);timer.start(700)
update();window.show();sys.exit(app.exec_())
'''


def packages(value):
    if (not isinstance(value, list) or not 1 <= len(value) <= 50
            or any(not isinstance(name, str) or not PACKAGE.fullmatch(name) for name in value)
            or len(set(value)) != len(value)):
        raise ValueError('Bitte 1 bis 50 verschiedene Debian-Paketnamen auswählen.')
    return sorted(value)


def plan(names, host, debs=None):
    import apt
    import apt_pkg
    apt_pkg.config.set('APT::Install-Recommends', 'false')
    apt_pkg.config.set('Dir::Cache::pkgcache', '')
    apt_pkg.config.set('Dir::Cache::srcpkgcache', '')
    names = packages(names)
    release = platform.freedesktop_os_release()
    if (socket.gethostname() != host or not re.fullmatch(r'LAB-(?:0[1-8][AB]|Teacher)', host)
            or release.get('ID') != 'debian' or release.get('VERSION_CODENAME') != 'trixie'
            or subprocess.check_output(['dpkg','--print-architecture'], text=True).strip() != 'amd64'):
        raise ValueError('Gerät, Debian-Version oder Architektur passt nicht zum Softwarekatalog.')
    if debs:
        import deb_client
        return deb_client.plan(names,host,debs)
    cache = apt.Cache()
    already = []
    for name in names:
        if name not in cache:
            raise ValueError('Paket auf diesem Client nicht verfügbar: ' + name)
        package = cache[name]
        if package.is_installed:
            already.append(dict(name=name, version=package.installed.version))
            continue
        candidate = package.candidate
        if not candidate or not any(origin.origin in ('Debian','Debian-Security')
                                     and (origin.codename or '').split('-')[0] == 'trixie'
                                     and origin.trusted for origin in candidate.origins):
            raise ValueError('Kein passendes vertrauenswürdiges Debian-Paket: ' + name)
        package.mark_install(auto_fix=True, auto_inst=True, from_user=True)
    if cache.broken_count:
        raise ValueError('Paketabhängigkeiten können nicht vollständig aufgelöst werden.')
    changes = []
    for package in cache.get_changes():
        changes.append(dict(name=package.name,
                            action='remove' if package.marked_delete else
                            'upgrade' if package.is_installed else 'install',
                            old=package.installed.version if package.is_installed else None,
                            version=package.candidate.version if package.candidate else None))
        if package.marked_downgrade:
            raise ValueError('Die Auswahl würde ein Paket zurückstufen. Bitte Paketquellen prüfen.')
    changes.sort(key=lambda row: row['name'])
    result = dict(host=host, packages=names, changes=changes, already=already,
                  download_bytes=cache.required_download, space_bytes=cache.required_space,
                  removals=[row['name'] for row in changes if row['action']=='remove'])
    result['digest'] = hashlib.sha256(json.dumps(result, sort_keys=True).encode()).hexdigest()
    return result


def progress_path():
    directory = Path('/run/cisco-lab-software')
    directory.mkdir(mode=0o755, exist_ok=True)
    info = directory.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise ValueError('LAB-Statusverzeichnis ist nicht geschützt.')
    directory.chmod(0o755)
    return directory / 'progress.json'


def write_progress(path, data):
    temporary = path.with_suffix('.new')
    temporary.unlink(missing_ok=True)
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
    os.fchmod(fd, 0o644)
    with os.fdopen(fd, 'w') as output:
        json.dump(data, output, ensure_ascii=False)
    temporary.replace(path)


def show_progress(path, identity):
    """Start the window as the active desktop user; no window on the greeter."""
    try:
        sessions = subprocess.check_output(['loginctl','list-sessions','--no-legend'], text=True, timeout=5)
        for line in sessions.splitlines():
            sid = line.split()[0]
            raw = subprocess.check_output(['loginctl','show-session',sid,'-p','Type','-p','Class',
                                          '-p','Active','-p','User','-p','Leader','-p','Remote'],text=True,timeout=5)
            fields = dict(row.split('=',1) for row in raw.splitlines() if '=' in row)
            if (fields.get('Active') != 'yes' or fields.get('Class') != 'user'
                    or fields.get('Type') not in ('x11','wayland') or fields.get('Remote') == 'yes'):
                continue
            uid = int(fields['User'])
            # The local classroom account cisco is intentionally UID 498.
            # logind's session class/type identify a desktop, not a UID cutoff.
            if uid <= 0:
                continue
            user = pwd.getpwuid(uid)
            environment = dict(PATH='/usr/bin:/bin', HOME=user.pw_dir, USER=user.pw_name,
                               XDG_RUNTIME_DIR=f'/run/user/{uid}',
                               DBUS_SESSION_BUS_ADDRESS=f'unix:path=/run/user/{uid}/bus')
            processes = [Path('/proc') / fields.get('Leader','0')]
            processes += [entry for entry in Path('/proc').iterdir() if entry.name.isdigit()]
            for entry in processes:
                try:
                    if entry.stat().st_uid != uid:
                        continue
                    values = dict(part.split(b'=',1) for part in (entry/'environ').read_bytes().split(b'\0') if b'=' in part)
                    if b'DISPLAY' not in values and b'WAYLAND_DISPLAY' not in values:
                        continue
                    for key in ('DISPLAY','WAYLAND_DISPLAY','XAUTHORITY'):
                        if key.encode() in values:
                            environment[key] = values[key.encode()].decode()
                    break
                except (OSError,ValueError,UnicodeError):
                    continue
            if 'DISPLAY' not in environment and 'WAYLAND_DISPLAY' not in environment:
                continue
            subprocess.Popen(['/usr/bin/python3','-B','-c',GUI,str(path),identity],
                             env=environment, user=uid, group=user.pw_gid,
                             extra_groups=os.getgrouplist(user.pw_name,user.pw_gid),
                             stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                             start_new_session=True)
            return True
    except (OSError,ValueError,KeyError,subprocess.SubprocessError):
        pass
    return False


def install(names, host, expected=None, debs=None):
    if os.geteuid() != 0:
        raise ValueError('Die Paketinstallation benötigt root.')
    current = plan(names, host, debs) if debs else plan(names, host)
    if current['removals']:
        raise ValueError('Installation würde Pakete entfernen. Auswahl anpassen.')
    if expected and current['digest'] != expected:
        raise ValueError('Paketstand seit der Vorschau geändert. Bitte Installation erneut prüfen.')
    path = progress_path()
    status = dict(id=uuid.uuid4().hex, packages=packages(names), status='running',
                  message='Paketinstallation wird vorbereitet …')
    write_progress(path, status)
    visible = show_progress(path, status['id'])
    changed = bool(current['changes'])
    try:
        if changed:
            requested = []
            changed_names = {row['name']:row for row in current['changes']}
            for name in names:
                if name in changed_names:
                    requested.append(name+'='+changed_names[name]['version'])
            if debs:
                import deb_client
                requested=deb_client.install_arguments(current,names,debs)
            command = ['/usr/bin/apt-get','-y','--no-remove','--no-install-recommends',
                       '-o','Dpkg::Options::=--force-confdef','-o','Dpkg::Options::=--force-confold',
                       '-o','DPkg::Lock::Timeout=60','install',*requested]
            environment = dict(os.environ, DEBIAN_FRONTEND='noninteractive', LC_ALL='C.UTF-8')
            lines = []
            with subprocess.Popen(command, stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
                                  text=True,env=environment) as worker:
                for line in worker.stdout:
                    lines = (lines+[line.strip()])[-5:]
                    status['message'] = '\n'.join(lines)[-1800:]
                    write_progress(path, status)
                if worker.wait():
                    raise ValueError('APT konnte die Software nicht vollständig installieren.\n'+'\n'.join(lines))
        status.update(status='succeeded',message='Die ausgewählte Software ist installiert. Dieses Fenster schließt sich automatisch.')
        write_progress(path, status)
        return dict(changed=changed, window_shown=visible, installed=current['packages'])
    except BaseException:
        status.update(status='failed',message='Installation fehlgeschlagen. Details stehen in der LAB-Verwaltung.')
        write_progress(path, status)
        raise


def main():
    if sys.argv[1]=='receive':
        import deb_client
        print(json.dumps(deb_client.receive(sys.stdin.buffer,sys.argv[2],sys.argv[3],int(sys.argv[4]))))
        return
    payload = json.loads(sys.stdin.read(32769))
    if not isinstance(payload, dict):
        raise ValueError('Ungültige Paketinstallation.')
    if sys.argv[1]=='cached':
        import deb_client
        result=deb_client.cached(payload['debs'],payload['host'])
    else:
        result = plan(payload['packages'],payload['host'],payload.get('debs')) if sys.argv[1]=='plan' else install(
            payload['packages'],payload['host'],payload.get('expected'),payload.get('debs'))
    print(json.dumps(result,ensure_ascii=False))


if __name__ == '__main__':
    try:
        main()
    except (ValueError,OSError,KeyError,subprocess.SubprocessError) as error:
        print(json.dumps({'error':str(error)},ensure_ascii=False))
        sys.exit(1)
