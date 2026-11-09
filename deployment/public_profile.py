"""Build a documentation profile without changing the operational source tree."""
import ipaddress
import json
from pathlib import Path
import re

PRIVATE_NETWORKS=tuple(map(ipaddress.ip_network,('10.0.0.0/8','172.16.0.0/12','192.168.0.0/16')))
STANDARD_NETWORK_LITERALS={'10.0.0.0','172.16.0.0','192.168.0.0'}
IPV4=re.compile(r'(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])')


def load_profile(root):
    path=Path(root)/'local-private/publication-profile.json'
    if not path.exists():return None
    if path.is_symlink() or path.stat().st_mode&0o077:
        raise ValueError('Private publication profile must have owner-only permissions.')
    value=json.loads(path.read_text())
    if set(value)!={'version','replace','files','preserve','forbidden'} or value['version']!=1:
        raise ValueError('Invalid private publication profile.')
    pairs=list(value['replace'].items())
    if any(not isinstance(a,str) or not a or not isinstance(b,str) for a,b in pairs):
        raise ValueError('Invalid publication substitutions.')
    return value


def sanitize(name,body,profile):
    if profile is None:return body
    try:text=body.decode('utf-8')
    except UnicodeDecodeError:return body
    # Protect intentionally public attribution before replacing deployment values.
    saved={f'__PUBLIC_ATTRIBUTION_{i}__':v for i,v in enumerate(profile['preserve'])}
    for marker,value in saved.items():text=text.replace(value,marker)
    pairs=list(profile.get('files',{}).get(name,{}).items())+list(profile['replace'].items())
    for old,new in pairs:
        for escapes in (2,1,0):
            old_form=old.replace('.', '\\'*escapes+'.')
            new_form=new.replace('.', '\\'*escapes+'.')
            text=text.replace(old_form,new_form)
    for marker,value in saved.items():text=text.replace(marker,value)
    if name=='deployment/apps/RESTORE.txt':
        text=('OEFFENTLICHE BEISPIELANLEITUNG / PUBLIC EXAMPLE GUIDE\n'
              'Adressen, Domains und Konten sind Beispielwerte. Fuer eine echte\n'
              'Wiederherstellung die konkreten Werte aus der privaten Sicherung verwenden.\n\n'+text)
    return text.encode()


def validate_profile(files,profile=None):
    for name,body in files.items():
        try:text=body.decode('utf-8')
        except UnicodeDecodeError:continue
        normalized=text.replace('\\\\.','.').replace('\\.','.')
        for literal in IPV4.findall(normalized):
            try:address=ipaddress.ip_address(literal)
            except ValueError:continue
            if literal not in STANDARD_NETWORK_LITERALS and any(address in n for n in PRIVATE_NETWORKS):
                raise ValueError('Private deployment address in public file: '+name)
        if profile:
            for value in profile['preserve']:normalized=normalized.replace(value,'')
            if any(value in normalized for value in profile['forbidden']):
                raise ValueError('Private deployment identifier in public file: '+name)
