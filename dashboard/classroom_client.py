"""Read the verified USB LAB adapter. No files, profiles or routes are changed."""
from datetime import datetime, timezone
import ipaddress
import json
import os
from pathlib import Path
import pwd
import re
import socket
import stat
import subprocess
import sys

SYS = Path('/sys/class/net')
MANIFEST = Path('/etc/cisco-lab/network.json')


def text(path):
    try:
        return path.read_text().strip()[:160]
    except OSError:
        return ''


def hardware(path):
    device = (path/'device').resolve()
    result = dict(manufacturer='',model='',usb_id='',driver=(device/'driver').resolve().name
                  if (device/'driver').exists() else '')
    for parent in (device,*device.parents):
        vendor, product = text(parent/'idVendor'),text(parent/'idProduct')
        if re.fullmatch('[0-9a-fA-F]{4}',vendor) and re.fullmatch('[0-9a-fA-F]{4}',product):
            result.update(manufacturer=text(parent/'manufacturer'),model=text(parent/'product'),
                          usb_id=vendor.lower()+':'+product.lower())
            break
    return result


def command(*args):
    return json.loads(subprocess.check_output(['/usr/sbin/ip','-j',*args],text=True,timeout=5))


def routes(rows, interface, family):
    result = []
    for row in rows:
        paths = [row] if row.get('dev')==interface else row.get('nexthops',[])
        for path in paths:
            if path.get('dev')!=interface:
                continue
            destination = row.get('dst','default')
            if destination!='default':
                network = ipaddress.ip_network(destination,strict=False)
                if network.version!=family:
                    continue
                destination = str(network)
            gateway = path.get('gateway',row.get('gateway'))
            source = row.get('prefsrc',row.get('src'))
            if gateway:
                gateway = str(ipaddress.ip_address(gateway))
            if source:
                source = str(ipaddress.ip_address(source))
            result.append(dict(destination=destination,gateway=gateway,source=source,
                table=row.get('table','main'),metric=row.get('metric'),protocol=row.get('protocol',''),
                type=row.get('type','unicast'),scope=row.get('scope',''),flags=row.get('flags',[])))
    return sorted(result,key=lambda row:(str(row['table']),row['destination'],row['gateway'] or ''))


def desktop_users():
    """Current local graphical sessions, including a locked desktop; never SSH."""
    try:
        listing = subprocess.check_output(['/usr/bin/loginctl','list-sessions','--no-legend','--no-pager'],
                                          text=True,stderr=subprocess.DEVNULL,timeout=3)
        ids = [line.split()[0] for line in listing.splitlines() if line.strip()]
        if not ids:
            return []
        properties = ['Name','User','Class','Type','Remote','State','LockedHint']
        output = subprocess.check_output(['/usr/bin/loginctl','show-session',*ids,'--all','--no-pager',
                                          *(arg for key in properties for arg in ('-p',key))],
                                         text=True,stderr=subprocess.DEVNULL,timeout=3)
        users = {}
        for block in output.strip().split('\n\n'):
            fields = dict(line.split('=',1) for line in block.splitlines() if '=' in line)
            if (not fields.get('Class','').startswith('user') or fields.get('Type') not in ('x11','wayland')
                    or fields.get('Remote')!='no' or fields.get('State')=='closing'):
                continue
            uid = int(fields['User'])
            if uid in users:
                users[uid]['locked'] &= fields.get('LockedHint')=='yes'
                continue
            account,kind = fields['Name'],'unknown'
            try:
                owner = pwd.getpwuid(uid)
                account = owner.pw_name
                kind = 'iserv' if Path(owner.pw_dir).parent==Path('/var/lib/iserv/client/home') else 'local'
            except KeyError:
                pass
            users[uid] = dict(account=account,kind=kind,locked=fields.get('LockedHint')=='yes')
        return sorted(users.values(),key=lambda user:user['account'])
    except (OSError,ValueError,KeyError,subprocess.SubprocessError):
        return None  # An unavailable session read must not mean nobody is logged in.


def collect_adapter(host):
    if not re.fullmatch(r'LAB-0[1-8][AB]',host) or socket.gethostname().split('.')[0]!=host:
        raise ValueError('identity')
    try:
        with os.fdopen(os.open(MANIFEST,os.O_RDONLY|os.O_NOFOLLOW),'r') as source:
            info = os.fstat(source.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid!=0 or info.st_mode&0o022 or info.st_size>16384:
                raise ValueError('manifest')
            config = json.load(source)
    except FileNotFoundError:
        raise ValueError('manifest') from None
    try:
        desired = config['identity']['lab']
        school = config['identity']['school']
        if (config['host']!=host or config['version']!=1 or config['group']!='cisco.lab'
                or desired['usb'] is not True or school['usb'] is not False
                or desired['mac'].lower()==school['mac'].lower()):
            raise ValueError('manifest')
    except (KeyError,TypeError,AttributeError):
        raise ValueError('manifest') from None
    matches = [path for path in SYS.iterdir() if text(path/'address').lower()==desired['mac'].lower()
               and text(path/'type')=='1' and not (path/'wireless').exists()
               and any(part.startswith('usb') for part in path.resolve().parts)]
    if not matches:
        return dict(state='missing',interface=None,hardware=None,ipv4=None,ipv6=None,routes4=None,routes6=None)
    if len(matches)!=1:
        raise ValueError('adapter-identity')
    path = matches[0]
    addresses = command('address','show','dev',path.name)
    if len(addresses)!=1:
        raise ValueError('adapter-identity')
    adapter = addresses[0]
    if adapter.get('address','').lower()!=desired['mac'].lower():
        raise ValueError('adapter-identity')
    flags = adapter.get('flags',[])
    admin_up,carrier = 'UP' in flags,'LOWER_UP' in flags
    families = {'inet':[],'inet6':[]}
    for address in adapter.get('addr_info',[]):
        if address.get('family') in families:
            value = ipaddress.ip_interface(str(address['local'])+'/'+str(address['prefixlen']))
            labels = [label for label in ('tentative','dadfailed','deprecated')
                      if address.get(label) or label in address.get('flags',[])]
            families[address['family']].append(dict(address=str(value),scope=address.get('scope',''),flags=labels))
    return dict(state='up' if admin_up and carrier else 'down',interface=path.name,
                mac=desired['mac'].lower(),admin_up=admin_up,carrier=carrier,hardware=hardware(path),
                ipv4=families['inet'],ipv6=families['inet6'],
                routes4=routes(command('-4','route','show','table','all'),path.name,4),
                routes6=routes(command('-6','route','show','table','all'),path.name,6))


def collect(host):
    result = collect_adapter(host)
    result['users'] = desktop_users()
    return result


if __name__=='__main__':
    try:
        data = collect(sys.argv[1])
        data['observed_at'] = datetime.now(timezone.utc).isoformat()
        print(json.dumps(dict(ok=True,host=sys.argv[1],data=data)))
    except (ValueError,OSError,KeyError,subprocess.SubprocessError) as error:
        code = str(error) if str(error) in ('identity','manifest','adapter-identity') else 'read'
        print(json.dumps(dict(ok=False,code=code)))
