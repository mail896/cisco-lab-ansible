"""Inspect, receive and plan explicit LAB-owned Debian packages; no shell commands."""
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import stat
import subprocess
import tempfile

MAX_DEB = 512 * 1024 * 1024
CACHE = Path('/var/cache/cisco-lab-software')
FIELDS = {'name', 'version', 'architecture', 'sha256', 'size'}
ENV = dict(os.environ, LC_ALL='C', DEBIAN_FRONTEND='noninteractive')


def metadata(path):
    result = subprocess.run(['dpkg-deb', '-f', str(path), 'Package', 'Version', 'Architecture', 'Description'],
                            capture_output=True, text=True, timeout=30)
    if result.returncode or len(result.stdout)>131072:
        raise ValueError('Keine lesbare Debian-Paketdatei.')
    fields = dict(line.split(': ', 1) for line in result.stdout.splitlines()
                  if not line.startswith(' ') and ': ' in line)
    row = dict(name=fields.get('Package',''), version=fields.get('Version',''),
               architecture=fields.get('Architecture',''), description=fields.get('Description','')[:500])
    if (not re.fullmatch(r'[a-z0-9][a-z0-9+.-]{1,127}',row['name'])
            or not re.fullmatch(r'[0-9][A-Za-z0-9.+:~\-]{0,127}',row['version'])
            or row['architecture'] not in ('amd64','all')):
        raise ValueError('Debian-Paketname, Version oder Architektur passt nicht zum LAB (amd64/all).')
    return row


def descriptors(rows):
    if not isinstance(rows,list) or len(rows)>50:
        raise ValueError('Ungültige eigene Paketauswahl.')
    names=set()
    for row in rows:
        if (not isinstance(row,dict) or set(row)!=FIELDS
                or not isinstance(row['name'],str) or not re.fullmatch(r'[a-z0-9][a-z0-9+.-]{1,127}',row['name'])
                or not isinstance(row['sha256'],str) or not re.fullmatch('[a-f0-9]{64}',row['sha256'])
                or not isinstance(row['version'],str) or not re.fullmatch(r'[0-9][A-Za-z0-9.+:~\-]{0,127}',row['version'])
                or row['architecture'] not in ('amd64','all') or type(row['size']) is not int
                or not 1<=row['size']<=MAX_DEB or row['name'] in names):
            raise ValueError('Ungültiges eigenes Debian-Paket.')
        names.add(row['name'])
    return sorted(rows,key=lambda row:row['name'])


def require_client(host):
    if os.geteuid()!=0 or socket.gethostname()!=host or not re.fullmatch(r'LAB-(?:0[1-8][AB]|Teacher)',host):
        raise ValueError('Eigene Pakete ausschließlich auf dem angegebenen LAB-Rechner.')


def cached(rows,host):
    require_client(host)
    found=[]
    for row in descriptors(rows):
        path=CACHE/(row['sha256']+'.deb')
        try:info=path.lstat()
        except FileNotFoundError:continue
        if stat.S_ISREG(info.st_mode) and info.st_uid==0 and not info.st_mode&0o022 and info.st_size==row['size']:
            found.append(row['sha256'])
    return dict(cached=found)


def receive(stream,host,digest,size):
    require_client(host)
    if not re.fullmatch('[a-f0-9]{64}',digest) or type(size) is not int or not 1<=size<=MAX_DEB:
        raise ValueError('Ungültiger Pakettransfer.')
    CACHE.mkdir(mode=0o755,exist_ok=True)
    info=CACHE.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid!=0 or info.st_mode&0o022:
        raise ValueError('LAB-Paketcache ist nicht geschützt.')
    digest_actual=hashlib.sha256();remaining=size
    with tempfile.NamedTemporaryFile(prefix='.receive-',dir=CACHE,delete=False) as out:
        temporary=Path(out.name)
        try:
            while remaining:
                block=stream.read(min(1048576,remaining))
                if not block:raise ValueError('Pakettransfer unvollständig.')
                out.write(block);digest_actual.update(block);remaining-=len(block)
            if stream.read(1) or digest_actual.hexdigest()!=digest:
                raise ValueError('Pakettransfer oder Prüfsumme stimmt nicht.')
            out.flush();os.fsync(out.fileno());metadata(temporary)
            os.fchmod(out.fileno(),0o644)
            temporary.replace(CACHE/(digest+'.deb'))
        finally:temporary.unlink(missing_ok=True)
    return dict(received=digest,size=size)


def plan(names,host,rows):
    import apt
    require_client(host);rows=descriptors(rows)
    if any(row['name'] not in names for row in rows):raise ValueError('Eigene Pakete gehören nicht zur Auswahl.')
    if len(cached(rows,host)['cached'])!=len(rows):raise ValueError('Eigene Paketdatei fehlt. Installation erneut prüfen.')
    cache=apt.Cache();own={row['name']:row for row in rows};already=[];requested=[]
    for name in names:
        row=own.get(name);installed=cache[name].installed if name in cache else None
        if row:
            path=CACHE/(row['sha256']+'.deb');actual=metadata(path)
            if any(actual[key]!=row[key] for key in ('name','version','architecture')):
                raise ValueError('Paketdatei und Vorschau stimmen nicht überein.')
            if installed and subprocess.run(['dpkg','--compare-versions',installed.version,'gt',row['version']]).returncode==0:
                raise ValueError('Eigene Paketversion ist älter als installiert: '+name)
            needed=not installed or installed.version!=row['version']
            if needed:requested.append(str(path))
        else:
            if name not in cache:raise ValueError('Paket nicht verfügbar: '+name)
            candidate=cache[name].candidate
            needed=not installed
            if needed:
                if not candidate or not any(o.trusted and o.origin in ('Debian','Debian-Security')
                        and (o.codename or '').split('-')[0]=='trixie' for o in candidate.origins):
                    raise ValueError('Kein vertrauenswürdiges Debian-Paket: '+name)
                requested.append(name)
        if not needed:already.append(dict(name=name,version=installed.version))
    changes=[]
    if requested:
        result=subprocess.run(['apt-get','-s','--no-remove','--no-install-recommends','install',*requested],
                              capture_output=True,text=True,env=ENV,timeout=90)
        if result.returncode:
            raise ValueError('Abhängigkeiten nicht installierbar: '+(result.stderr or result.stdout)[-1500:])
        for line in result.stdout.splitlines():
            if line.startswith('Remv '):raise ValueError('Die Auswahl würde Pakete entfernen.')
            if not line.startswith('Inst '):continue
            match=re.match(r'^Inst (\S+)(?: \[([^\]]+)\])? \((\S+)',line)
            if not match:raise ValueError('APT-Vorschau konnte nicht gelesen werden.')
            name,old,version=match.groups();name=name.split(':')[0]
            if old and subprocess.run(['dpkg','--compare-versions',old,'gt',version]).returncode==0:
                raise ValueError('Die Auswahl würde ein Paket zurückstufen: '+name)
            changes.append(dict(name=name,action='upgrade' if old else 'install',old=old,version=version))
    result=dict(host=host,packages=names,changes=sorted(changes,key=lambda row:row['name']),already=already,
                removals=[],download_bytes=sum(cache[row['name']].candidate.size for row in changes
                    if row['name'] not in own and row['name'] in cache and cache[row['name']].candidate),
                space_bytes=None,deb_files=rows)
    result['digest']=hashlib.sha256(json.dumps(result,sort_keys=True).encode()).hexdigest()
    return result


def install_arguments(current,names,rows):
    own={row['name']:row for row in descriptors(rows)};changed={row['name']:row for row in current['changes']}
    return [str(CACHE/(own[name]['sha256']+'.deb')) if name in own else name+'='+changed[name]['version']
            for name in names if name in changed]
