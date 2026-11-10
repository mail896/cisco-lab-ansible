"""Stored classroom exercises. Generation is offline and never touches a device."""
from datetime import datetime, timezone
from collections import OrderedDict
from copy import deepcopy
import hashlib
import hmac
import ipaddress as ip
import json
import os
from pathlib import Path
import random
import re
import secrets
import threading
import tempfile
import uuid

from dashboard.pagination import paginate
from dashboard.targets import LAB_TARGETS
from dashboard.exercise_richtext import rich_html, plain_text
from maintenance.preview import BERLIN

DEFAULT_TEXT = dict(
    title='LAB-Routing Übungen',
    introduction='Verbinden Sie LAN A und LAN B über zwei Router. Prüfen Sie die Erreichbarkeit in beide Richtungen mit {{familien}}.',
    work='<ol><li>Prüfen Sie die <strong>Verkabelung</strong> und setzen Sie die Cisco-Geräte bei Bedarf zurück.</li><li>Ermitteln und dokumentieren Sie die <strong>Geräteadressen</strong> anhand der vorgegebenen Netze und des Adressschemas. Konfigurieren Sie Router, Switche und USB-Labornetzadapter.</li><li>Richten Sie gezielte <strong>statische Routen</strong> zum jeweils anderen LAN ein.</li><li>Prüfen Sie zuerst den lokalen Router, dann das Transfernetz und zuletzt den anderen PC. Prüfen Sie auch den Rückweg.</li><li>Dokumentieren Sie einen <strong>Fehler</strong>, Ihre Prüfung und die Korrektur.</li></ol>',
    hints='Switch: Port 1 zum PC, Port 24 zum Router. Router: Gi0/0/0 zum Switch, Gi0/0/1 zum anderen Router. Lassen Sie das Schulnetz unverändert. Nutzen Sie am PC den Dialog „Labornetz einstellen“; tragen Sie keine Default-Route ins LAB ein.',
    reset='Setzen Sie nur die zugewiesenen Übungsgeräte zurück. Öffnen Sie die Konsole: enable, erase startup-config. Prüfen Sie beim Switch zusätzlich dir flash: und entfernen Sie eine vorhandene vlan.dat mit delete flash:vlan.dat. Starten Sie danach mit reload neu; beantworten Sie eine Nachfrage zum Speichern der alten Konfiguration mit no. Lehnen Sie den initialen Setup-Dialog ab. Konfigurieren Sie erst nach dem Neustart die neue Übung.',
    reset_ipv6='2960: IPv6-Management benötigt ein geeignetes IOS/SDM-Profil. Mit show sdm prefer prüfen. Falls nötig einmalig: enable → configure terminal → sdm prefer dual-ipv4-and-ipv6 default → end → copy running-config startup-config → reload. Fügen Sie erst nach diesem Neustart die S1/S2-Konfiguration ein. Layer-2-Weiterleitung von IPv6 benötigt keine IPv6-Managementadresse.',
    ws_title='Wireshark: die Ping-Strecke untersuchen',
    ws_work='<p>Öffnen Sie Wireshark und wählen Sie „USB-Labornetz“ als Aufzeichnungsschnittstelle. Starten Sie die Aufzeichnung vor dem Ping und stoppen Sie sie danach.</p><p>Anzeigefilter: <strong>{{filter}}</strong>. Das ist der Filter über der Paketliste, kein Capture-Filter.</p><p>Senden Sie je vier Echo-Anfragen in beide Richtungen. Ordnen Sie Anfrage und Antwort zu und dokumentieren Sie Quell-/Ziel-IP, Kennung, Sequenz sowie Antwortzeit.</p>',
    ws_advanced='<p>Vergleichen Sie Mitschriften von PC-A und PC-B: Welche MAC-Adressen ändern sich, welche IP-Adressen bleiben gleich? Wie verändert sich TTL/Hop Limit? Begründen Sie Ihre Beobachtungen.</p>',
    ws_solution='<p>Echo Request und Echo Reply über Kennung/Sequenz zusammenführen. Die IP-Endpunkte bleiben beim Routing gleich; Ethernet-Adressen gehören jeweils zum lokalen Link.</p><p>Bei beiden Endpunkten mitschneiden und echte Beobachtungen dokumentieren; konkrete Laufzeiten und Paketnummern werden nicht vorgegeben.</p>',
    ws_solution_advanced='<p>Vergleichen Sie dieselbe Echo-Anfrage am Sender und Empfänger: TTL/Hop Limit ist nach den zwei Routern um 2 kleiner. ARP/NDP erscheint nur bei nötiger Nachbarauflösung; ein warmer Cache ist kein Fehler.</p>',
)
DEFAULT_OPTIONS = dict(ipv4=True, ipv6=True, level='plan', teams=1,
                       sizes='fixed', lan_prefix=29, transfer_prefix=30,
                       lan_min=24, lan_max=29, transfer_min=29, transfer_max=30,
                       addresses='fixed', wireshark=False, ws_level='guided')
TEXT_LIMITS = dict(title=100, introduction=6000, work=16000, hints=8000, reset=8000, reset_ipv6=8000,
                   ws_title=120, ws_work=8000, ws_advanced=6000, ws_solution=8000, ws_solution_advanced=6000)
PLACEHOLDERS = {'familien', 'team', 'lan_a', 'lan_b', 'transfer', 'filter'}
PROTECTED = tuple({ip.ip_network(address + '/16', strict=False) for address in LAB_TARGETS.values()})
POOLS = tuple(ip.ip_network(value) for value in ('192.168.0.0/16', '172.16.0.0/12', '10.0.0.0/16'))


def formal_standard(text):
    # Correct the former built-in wording when older snapshots/templates are
    # rendered; their saved networks and individual teacher prose stay intact.
    if any(phrase in text for phrase in ('Verbinde LAN A und LAN B über zwei Router',
                                        'Prüfe die Verkabelung und setze die Cisco-Geräte',
                                        'Nutze am PC den Dialog')):
        for old,new in (('Verbinde','Verbinden Sie'),('Prüfe','Prüfen Sie'),('setze','setzen Sie'),
                        ('Plane','Planen Sie'),('Konfiguriere','Konfigurieren Sie'),
                        ('Richte','Richten Sie'),('Dokumentiere','Dokumentieren Sie'),('Nutze','Nutzen Sie')):
            text=re.sub(r'\b'+old+r'\b',new,text)
        text=text.replace('deine Prüfung','Ihre Prüfung')
    return text


def texts(value):
    if (not isinstance(value, dict) or set(value) - set(TEXT_LIMITS)
            or not {'title', 'introduction', 'work', 'hints'} <= set(value)):
        raise ValueError('Titel, Einleitung, Arbeitsaufträge und Hinweise angeben.')
    # Older snapshots predate editable reset sections; preserve their former text.
    value = dict(DEFAULT_TEXT, **value)
    for key, maximum in TEXT_LIMITS.items():
        text = value[key]
        if (not isinstance(text, str) or len(text) > maximum or '\x00' in text
                or any(ord(c) < 32 and c not in '\n\r\t' for c in text)):
            raise ValueError('Ungültiger oder zu langer Vorlagentext: ' + key)
        if set(re.findall(r'\{\{(.*?)\}\}', text)) - PLACEHOLDERS:
            raise ValueError('Unbekannter Platzhalter. Erlaubt: ' + ', '.join(sorted(PLACEHOLDERS)))
    if not value['title'].strip() or not plain_text(value['work']):
        raise ValueError('Titel und Arbeitsaufträge dürfen nicht leer sein.')
    return {key: value[key].strip() if key in ('title','ws_title') else rich_html(formal_standard(value[key].strip())) for key in TEXT_LIMITS}


def options(value):
    if not isinstance(value, dict) or set(value) - set(DEFAULT_OPTIONS):
        raise ValueError('Unbekannte Aufgabeneinstellung.')
    result = dict(DEFAULT_OPTIONS, **value)
    for key in ('ipv4', 'ipv6', 'wireshark'):
        if type(result[key]) is not bool:
            raise ValueError('IP-Versionen und Wireshark als Auswahl angeben.')
    if not (result['ipv4'] or result['ipv6']):
        raise ValueError('Mindestens IPv4 oder IPv6 auswählen.')
    for key, choices in dict(level=('configure', 'plan'), sizes=('fixed', 'random'),
                             addresses=('fixed', 'random'), ws_level=('guided', 'advanced')).items():
        if result[key] not in choices:
            raise ValueError('Ungültige Auswahl: ' + key)
    for key, lower, upper in (('teams', 1, 8), ('lan_prefix', 16, 29), ('transfer_prefix', 24, 30),
                               ('lan_min', 16, 29), ('lan_max', 16, 29),
                               ('transfer_min', 24, 30), ('transfer_max', 24, 30)):
        if type(result[key]) is not int or not lower <= result[key] <= upper:
            raise ValueError('Ungültiger Zahlenbereich: ' + key)
    if result['lan_min'] > result['lan_max'] or result['transfer_min'] > result['transfer_max']:
        raise ValueError('Der Präfixbereich muss aufsteigend sein.')
    smallest = result['lan_min'] if result['sizes'] == 'random' else result['lan_prefix']
    if result['ipv4'] and (1 << (smallest - 16)) < result['teams']:
        raise ValueError('Für diese Teamanzahl kleinere LAN-Netze wählen (größere Präfixzahl); LAN A hat insgesamt einen /16-Adressbereich.')
    return result


def allocate(pool, prefix, rng, used):
    """Pick a subnet without enumerating hosts or the entire address pool."""
    count = 1 << (prefix - pool.prefixlen)
    first = rng.randrange(count)
    for offset in range(count):
        index = (first + offset) % count
        network = ip.ip_network((int(pool.network_address) + (index << (32 - prefix)), prefix))
        if not any(network.overlaps(old) for old in (*used, *PROTECTED)):
            used.append(network)
            return network
    raise ValueError('Die gewählten Netzgrößen passen nicht für alle Teams in den LAB-Adressbereich.')


def generate(settings, seed):
    cfg = options(settings)
    rng = random.Random(seed)
    used = []
    used6 = set()
    variants = []
    for number in range(1, cfg['teams'] + 1):
        nets = {}
        for position, segment in enumerate(('a', 'b', 'transfer')):
            prefix = (cfg['transfer_prefix'] if position == 2 else cfg['lan_prefix'])
            if cfg['sizes'] == 'random':
                low, high = ('transfer_min', 'transfer_max') if position == 2 else ('lan_min', 'lan_max')
                prefix = rng.randint(cfg[low], cfg[high])
            net = {}
            if cfg['ipv4']:
                v4 = allocate(POOLS[position], prefix, rng, used)
                net.update(ipv4=str(v4), mask=str(v4.netmask))
            if cfg['ipv6']:
                subnet = rng.randrange(1, 1 << 32)
                while subnet in used6:
                    subnet = rng.randrange(1, 1 << 32)
                used6.add(subnet)
                v6 = ip.ip_network((int(ip.ip_address('2001:db8::')) + (subnet << 64), 64))
                net['ipv6'] = str(v6)
            nets[segment] = net
        devices = {name: dict(name=name, model='ISR4331' if name.startswith('R') else
                              '2960-24TT' if name.startswith('S') else 'Intel NUC', interfaces=[], routes=[])
                   for name in ('PC-A', 'S1', 'R1', 'R2', 'S2', 'PC-B')}
        def addresses(segment, count):
            net = nets[segment]
            result = [dict() for _ in range(count)]
            for family in ('ipv4', 'ipv6'):
                if family not in net:
                    continue
                network = ip.ip_network(net[family])
                last = network.num_addresses - (2 if family == 'ipv4' else 1)
                values = ([1, 2] if count == 2 else [1, 2, last if family == 'ipv4' else 6])
                if cfg['addresses'] == 'random':
                    values = rng.sample(range(1, min(last, 65534) + 1), count)
                for row, offset in zip(result, values):
                    row[family] = str(network.network_address + offset) + '/' + str(network.prefixlen)
            return result
        for segment, router, switch, pc in (('a', 'R1', 'S1', 'PC-A'), ('b', 'R2', 'S2', 'PC-B')):
            r, s, p = addresses(segment, 3)
            r.update(port='GigabitEthernet0/0/0', segment=segment)
            if cfg['ipv6']:
                r['link_local'] = 'fe80::1'
            s.update(port='Vlan1', segment=segment)
            p.update(port='LAN-USB', segment=segment)
            devices[router]['interfaces'].append(r)
            devices[switch]['interfaces'].append(s)
            devices[pc]['interfaces'].append(p)
        for router, row in zip(('R1', 'R2'), addresses('transfer', 2)):
            row.update(port='GigabitEthernet0/0/1', segment='transfer')
            devices[router]['interfaces'].append(row)
        for local, remote, pc, segment in (('R1', 'R2', 'PC-A', 'b'), ('R2', 'R1', 'PC-B', 'a')):
            for family in ('ipv4', 'ipv6'):
                if family not in nets[segment]:
                    continue
                dest = nets[segment][family]
                gateway = str(ip.ip_interface(devices[remote]['interfaces'][1][family]).ip)
                devices[local]['routes'].append(dict(family=family, destination=dest, gateway=gateway))
                gateway = str(ip.ip_interface(devices[local]['interfaces'][0][family]).ip)
                devices[pc]['routes'].append(dict(family=family, destination=dest, gateway=gateway))
        variants.append(dict(team=number, networks=nets, devices=devices))
    return variants


def expanded(task, variant):
    def network(key):
        return ' / '.join(variant['networks'][key].get(f, '') for f in ('ipv4', 'ipv6') if f in variant['networks'][key])
    values = dict(familien=' und '.join(f for f, yes in (('IPv4', task['options']['ipv4']), ('IPv6', task['options']['ipv6'])) if yes),
                  team=str(variant['team']), lan_a=network('a'), lan_b=network('b'), transfer=network('transfer'),
                  filter=' || '.join(f for f,yes in (('icmp',task['options']['ipv4']),('icmpv6',task['options']['ipv6'])) if yes))
    return {key: re.sub(r'\{\{(.*?)\}\}', lambda m: values[m[1]], text) for key, text in texts(task['text']).items()}


def principal(identity):
    # Ownership is derived only from the authenticated identity, never the body.
    return hashlib.sha256((identity.get('source', '') + ':' + identity.get('uuid', identity['account'])).encode()).hexdigest()


def team_names(value, count):
    if value is None:
        return [''] * count
    if (not isinstance(value, list) or len(value) != count
            or any(not isinstance(name, str) or len(name) > 160
                   or any(ord(c) < 32 for c in name) for name in value)):
        raise ValueError('Je Team einen Namen mit höchstens 160 Zeichen angeben.')
    return [name.strip() for name in value]


def task_label(row):
    date=datetime.fromisoformat(row['created']).astimezone(BERLIN).strftime('%d.%m.%Y %H:%M')
    cfg=row['options'];family='Dual Stack' if cfg['ipv4'] and cfg['ipv6'] else 'IPv4' if cfg['ipv4'] else 'IPv6'
    names=' / '.join(name for name in row.get('team_names', []) if name)
    return row['title']+(' · '+names if names else '')+' · '+family+' · '+str(cfg['teams'])+' Team(s) · '+date+' · '+row['id'][:6]


class Exercises:
    def __init__(self, directory):
        self.directory = Path(directory)
        for path in (self.directory, self.directory/'tasks', self.directory/'templates'):
            path.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.lock = threading.RLock()
        # Unsaved previews are bounded and private to their authenticated author.
        self.drafts = OrderedDict()

    @staticmethod
    def validate_id(value):
        if not isinstance(value, str) or not re.fullmatch(r'[a-f0-9]{32}', value):
            raise ValueError('Ungültige Aufgabenkennung.')

    def read(self, kind, identity):
        self.validate_id(identity)
        return json.loads((self.directory/kind/(identity+'.json')).read_text())

    def write(self, kind, row):
        directory = self.directory/kind
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=directory, delete=False) as output:
            temporary = Path(output.name)
            try:
                json.dump(row, output, ensure_ascii=False)
                output.flush()
                os.fsync(output.fileno())
                os.replace(temporary, directory/(row['id']+'.json'))
            finally:
                temporary.unlink(missing_ok=True)

    def editable(self, row, user):
        return user.get('role') == 'admin' or row['owner'] == principal(user)

    def public(self, row, user):
        result = deepcopy({key: value for key, value in row.items() if key not in ('owner', 'address_pool')})
        result['editable'] = self.editable(row, user)
        result['text']=texts(row['text'])
        if 'created' in row:
            result['label']=task_label(row)
            result['team_names']=team_names(row.get('team_names'), row['options']['teams'])
        return result

    def state(self, user, page=1):
        with self.lock:
            tasks = [json.loads(p.read_text()) for p in (self.directory/'tasks').glob('*.json')]
            tasks.sort(key=lambda row: (row['created'], row['id']), reverse=True)
            page_data = paginate(dict(runs=tasks), page)
            rows = [dict({key: row[key] for key in ('id', 'title', 'created', 'author', 'options')},
                         label=task_label(row), editable=self.editable(row, user),
                         networks=[dict(team=v['team'], networks=v['networks']) for v in row['variants']]) for row in page_data['runs']]
            templates = [self.public(json.loads(p.read_text()), user) for p in (self.directory/'templates').glob('*.json')]
            templates.sort(key=lambda row: row['title'].casefold())
            deletion=self.delete_preview(user, tasks)
            # Summaries contain no device/configuration trees or hidden families.
            page_data['runs'] = rows
            return dict(page_data, templates=templates, defaults=self.defaults(), deletion=deletion)

    def defaults(self):
        path = self.directory/'standard.json'
        row = json.loads(path.read_text()) if path.exists() else dict(text=DEFAULT_TEXT, options=DEFAULT_OPTIONS)
        return dict(text=texts(row['text']), options=options(row['options']))

    def standard(self, text, settings, user):
        if user.get('role') != 'admin':
            raise PermissionError('Nur Administratoren dürfen die Standardvorlage ändern.')
        row = dict(id='standard', text=texts(text), options=options(settings))
        row['options'].update(level='plan', addresses='fixed')
        with self.lock:
            self.write('', row)
        return self.defaults()

    def task(self, identity, user):
        with self.lock:
            row, saved = self.lookup(identity, user)
            return dict(self.public(row, user), saved=saved)

    def lookup(self, identity, user):
        self.validate_id(identity)
        if identity in self.drafts:
            row = self.drafts[identity]
            if row['owner'] != principal(user):
                raise FileNotFoundError(identity)
            return row, False
        return self.read('tasks', identity), True

    def draft_copy(self, row, saved, user):
        result = deepcopy(row)
        if saved:
            result.update(id=uuid.uuid4().hex, created=datetime.now(timezone.utc).isoformat())
        result.update(owner=principal(user), author=user['name'])
        return result

    def remember(self, row):
        self.drafts[row['id']] = row
        self.drafts.move_to_end(row['id'])
        while len(self.drafts) > 128:
            self.drafts.popitem(last=False)

    def preview(self, settings, text, user, names=None):
        row = self.build(settings, text, user, names)
        with self.lock:
            self.remember(row)
        return dict(self.public(row, user), saved=False)

    def revise(self, identity, text, user, names=None):
        body = texts(text)
        with self.lock:
            current, saved = self.lookup(identity, user)
            previous_names = team_names(current.get('team_names'), current['options']['teams'])
            names = previous_names if names is None else team_names(names, current['options']['teams'])
            if texts(current['text']) == body and names == previous_names:
                return dict(self.public(current, user), saved=saved)
            row = self.draft_copy(current, saved, user)
            row.update(title=body['title'], text=body, team_names=names)
            self.remember(row)
            return dict(self.public(row, user), saved=False)

    def families(self, identity, ipv4, ipv6, text, user, names=None):
        with self.lock:
            current, _ = self.lookup(identity, user)
            cfg = options(dict(current['options'], ipv4=ipv4, ipv6=ipv6))
            task = self.revise(identity, text, user, names)
            if all(task['options'][f] == cfg[f] for f in ('ipv4','ipv6')):
                return task
            current, saved = self.lookup(task['id'], user)
            row = self.draft_copy(current, saved, user)
            pool = row.get('address_pool', row['variants'])
            for family in ('ipv4','ipv6'):
                if not cfg[family] or family in pool[0]['networks']['a']:
                    continue
                extra = generate(dict(cfg, ipv4=family=='ipv4', ipv6=family=='ipv6'), row['seed']+':'+family)
                for previous, addition in zip(pool, extra):
                    for segment, network in addition['networks'].items():
                        previous['networks'][segment].update(network)
                    for name, device in addition['devices'].items():
                        for old, new in zip(previous['devices'][name]['interfaces'], device['interfaces']):
                            old.update(new)
                        previous['devices'][name]['routes'].extend(device['routes'])
            row['address_pool'] = pool
            row['variants'] = deepcopy(pool)
            for variant in row['variants']:
                for network in variant['networks'].values():
                    if not ipv4:network.pop('ipv4', None);network.pop('mask', None)
                    if not ipv6:network.pop('ipv6', None)
                for device in variant['devices'].values():
                    for interface in device['interfaces']:
                        if not ipv4:interface.pop('ipv4', None)
                        if not ipv6:interface.pop('ipv6', None);interface.pop('link_local', None)
                    device['routes'] = [route for route in device['routes'] if cfg[route['family']]]
            row['options'] = cfg
            self.remember(row)
            return dict(self.public(row, user), saved=False)

    def save(self, identity, text, user, names=None):
        with self.lock:
            task = self.revise(identity, text, user, names)
            if task['saved']:
                return task
            row = self.drafts[task['id']]
            self.write('tasks', row)
            del self.drafts[task['id']]
            return dict(self.public(row, user), saved=True)

    def create(self, settings, text, user, names=None):
        # Compatibility for callers explicitly creating a saved snapshot.
        with self.lock:
            task = self.preview(settings, text, user, names)
            return self.save(task['id'], text, user)

    def build(self, settings, text, user, names=None):
        cfg, body = options(settings), texts(text)
        cfg.update(level='plan',addresses='fixed')
        seed = secrets.token_hex(16)
        row = dict(id=uuid.uuid4().hex, title=body['title'], created=datetime.now(timezone.utc).isoformat(),
                   author=user['name'], owner=principal(user), options=cfg, text=body, seed=seed,
                   variants=generate(cfg, seed), team_names=team_names(names, cfg['teams']), version=1)
        return row

    def template(self, identity, text, settings, user):
        body, cfg = texts(text), options(settings)
        cfg.update(level='plan',addresses='fixed')
        with self.lock:
            previous = self.read('templates', identity) if identity else None
            if previous and not self.editable(previous, user):
                raise PermissionError('Diese Vorlage gehört einer anderen Lehrkraft. Bitte als Kopie speichern.')
            row = dict(id=identity or uuid.uuid4().hex, title=body['title'], text=body, options=cfg,
                       owner=previous['owner'] if previous else principal(user), author=user['name'])
            self.validate_id(row['id'])
            self.write('templates', row)
            return self.public(row, user)

    def delete(self, kind, identity, user):
        if kind not in ('tasks', 'templates'):
            raise ValueError('Unbekannter Eintrag.')
        with self.lock:
            row = self.read(kind, identity)
            if not self.editable(row, user):
                raise PermissionError('Nur eigene Einträge können entfernt werden.')
            (self.directory/kind/(identity+'.json')).unlink()

    def deletable(self, user, rows=None):
        if rows is None:
            rows = (json.loads(path.read_text()) for path in (self.directory/'tasks').glob('*.json'))
        return sorted(row['id'] for row in rows if self.editable(row,user))

    def delete_preview(self, user, rows=None):
        ids=self.deletable(user, rows)
        return self.deletion_summary(user, ids)

    @staticmethod
    def deletion_summary(user, ids):
        token=hashlib.sha256((principal(user)+':'+','.join(ids)).encode()).hexdigest()
        return dict(count=len(ids),token=token,scope='all' if user.get('role')=='admin' else 'own')

    def delete_all(self, token, confirmed, user):
        if confirmed is not True or not isinstance(token,str):raise ValueError('Löschen ausdrücklich bestätigen.')
        with self.lock:
            ids=self.deletable(user)
            preview=self.deletion_summary(user, ids)
            if not hmac.compare_digest(preview['token'],token):
                raise ValueError('Die Sammlung hat sich geändert. Bitte neu laden und erneut bestätigen.')
            for identity in ids:(self.directory/'tasks'/(identity+'.json')).unlink()
            return dict(deleted=preview['count'])
