"""Linux CLI diagnosis exercises. Every scenario is simulated; no device is contacted.

A scenario is a small, self-consistent model of one Linux lab PC with a school
uplink and a USB lab adapter. One fault is applied to the healthy model and the
command outputs are rendered from the resulting state, so outputs, evidence and
the model solution always agree. Saved tasks keep the rendered text, so later
changes to this generator never alter an existing snapshot.
"""
from datetime import datetime
import ipaddress as ip
import random
import re

from dashboard.exercises import Exercises, allocate, team_names
from dashboard.exercise_richtext import rich_html, plain_text
from maintenance.preview import BERLIN

LEVELS = {'einstieg': 'Einstieg', 'fortgeschritten': 'Fortgeschritten', 'profi': 'Profi'}
THEMES = {'adressierung': 'Adressen', 'routing': 'Routing', 'dns': 'DNS', 'pfad': 'Pfad & MTU', 'dienste': 'Ports & Dienste'}

DEFAULT_TEXT = dict(
    title='LAB-Fehlersuche im Netz',
    introduction='<p>An einem Linux-Rechner im Labor ist eine Störung gemeldet worden. '
                 'Werten Sie die Störungsmeldung und die Befehlsausgaben aus. Stellen Sie fest, '
                 'was nicht stimmt und wie es sich beheben lässt.</p>',
    work='<ol><li>Beschreiben Sie das <strong>Symptom</strong> in eigenen Worten.</li>'
         '<li>Markieren Sie in den Ausgaben die Zeile, die den Fehler <strong>belegt</strong>.</li>'
         '<li>Benennen Sie die <strong>Ursache</strong>.</li>'
         '<li>Geben Sie den <strong>Befehl zur Korrektur</strong> an.</li>'
         '<li>Nennen Sie einen <strong>Prüfbefehl</strong> und die erwartete Ausgabe nach der Korrektur.</li></ol>',
    hints='<p>Die Ausgaben stammen von einem simulierten Rechner {{rechner}}; alle Adressen sind frei erfunden. '
          'Führen Sie die Korrekturbefehle nicht am Laborrechner aus. Befehle mit sudo verändern '
          'die Konfiguration und gehören hier nur auf das Arbeitsblatt.</p>',
)
DEFAULT_OPTIONS = dict(level='einstieg', teams=1, themes=['adressierung', 'routing', 'dns', 'pfad', 'dienste'], ipv6=False,
                       fault='zufall', part_b=True, part_a=False, part_c=False, wireshark=False)
TEXT_LIMITS = dict(title=100, introduction=6000, work=16000, hints=8000)
PLACEHOLDERS = {'team', 'rechner', 'niveau'}

SCHOOL_POOL = ip.ip_network('172.16.0.0/12')
LAB_POOL = ip.ip_network('192.168.0.0/16')
# Documentation ranges (RFC 5737/3849) stand in for any server on the Internet.
INTERNET4 = '198.51.100.10'
INTERNET6 = '2001:db8:ffff::10'
OUIS = ('3c:52:82', 'a0:36:9f', 'f8:75:a4', '00:e0:4c')

# (Themen, Befehl, Zweck); Befehlshilfe und Leitfragen folgen den gewählten Themen.
HELP = (
    (('adressierung', 'routing'), 'ip address', 'Zeigt Schnittstellen, Zustand (UP/DOWN), Adressen und Präfixe.'),
    (('routing',), 'ip route', 'Zeigt die IPv4-Routingtabelle mit Default-Route und direkt verbundenen Netzen.'),
    (('adressierung', 'routing'), 'ip -6 route', 'Zeigt die IPv6-Routingtabelle.'),
    (('routing',), 'ip route get <Ziel>', 'Zeigt, über welches Gateway und welche Schnittstelle ein Ziel erreicht würde.'),
    (('adressierung', 'routing'), 'ip neigh', 'Zeigt den ARP-/NDP-Cache: welche MAC-Adresse zu welcher IP gehört, Zustand z. B. REACHABLE oder FAILED.'),
    (('adressierung', 'routing', 'dns'), 'ping -c 3 <Ziel>', 'Sendet drei Echo-Anfragen und zeigt Antworten oder Fehlermeldungen.'),
    (('adressierung',), 'arping -D -I <Schnittstelle> <IP>', 'Prüft, ob ein anderes Gerät dieselbe IP-Adresse verwendet.'),
    (('dns',), 'cat /etc/resolv.conf', 'Zeigt DNS-Server (nameserver) und Suchdomain (search), die der Rechner verwendet.'),
    (('dns',), 'dig <Name>', 'Fragt den DNS-Server direkt; zeigt status (NOERROR, NXDOMAIN), Antwort, TTL und den befragten Server.'),
    (('dns',), 'nslookup <Name>', 'Einfache DNS-Abfrage mit Angabe des verwendeten Servers.'),
    (('dns',), 'getent hosts <Name>', 'Löst einen Namen so auf wie Programme (z. B. ping, Browser): erst /etc/hosts, dann DNS.'),
    (('pfad',), 'traceroute -n <Ziel>', 'Zeigt jede Zwischenstation (Hop) auf dem Weg zum Ziel; * * * bedeutet: keine Antwort von diesem Hop.'),
    (('pfad',), 'tracepath -n <Ziel>', 'Wie traceroute, ermittelt zusätzlich die MTU der Strecke (pmtu).'),
    (('pfad',), 'ping -M do -s <Größe> <Ziel>', 'Sendet Pakete mit gesetztem DF-Bit (nicht fragmentieren) und wählbarer Nutzdatengröße; IP-Paket = Größe + 28 Byte.'),
    (('pfad',), 'ip link show <Schnittstelle>', 'Zeigt Zustand und MTU einer Schnittstelle.'),
    (('dienste',), 'ss -tlnp', 'Zeigt, auf welchen Adressen und TCP-Ports Dienste lauschen (LISTEN).'),
    (('dienste',), 'curl -I <URL>', 'Ruft nur die Kopfzeilen einer Webseite ab; zeigt Erfolg oder die genaue Fehlermeldung.'),
    (('dienste',), 'nc -zv -w 3 <Ziel> <Port>', 'Prüft, ob ein einzelner TCP-Port erreichbar ist: succeeded, refused oder timed out.'),
    (('dienste',), 'nmap -p <Ports> <Ziel>', 'Prüft mehrere Ports (open, closed, filtered). Im LAB nur im Labornetz gegen den Partnerplatz verwenden.'),
    (('dienste',), 'systemctl status <Dienst>', 'Zeigt, ob ein Dienst läuft (active) oder gestoppt ist (inactive).'),
)
GUIDE = (
    (('adressierung', 'routing'), 'Welche Schnittstellen hat der Rechner, und welche ist UP?'),
    (('adressierung',), 'Welche IPv4-Adresse mit welchem Präfix hat die Schnittstelle zum Schulnetz?'),
    (('routing',), 'Welche Default-Route ist eingetragen, und liegt das Gateway im eigenen Netz?'),
    (('adressierung', 'routing'), 'Was genau meldet ping? „Network is unreachable“ und „Destination Host Unreachable“ bedeuten Verschiedenes.'),
    (('dns',), 'Funktioniert die Verbindung mit der IP-Adresse, aber nicht mit dem Namen?'),
    (('dns',), 'Welcher DNS-Server ist eingetragen, und antwortet er überhaupt?'),
    (('dns',), 'Was bedeutet „status: NXDOMAIN“ im Vergleich zu „timed out“ oder „no servers could be reached“?'),
    (('dns',), 'Woher bekommt ein Programm die Adresse: aus /etc/hosts oder vom DNS-Server?'),
    (('pfad',), 'Bis zu welchem Hop kommen die Pakete, und ab wo kommt keine Antwort mehr?'),
    (('pfad',), 'Wiederholen sich Adressen im traceroute?'),
    (('pfad',), 'Welche Paketgröße kommt noch durch, welche nicht? Rechnen Sie Nutzdaten + 28 Byte.'),
    (('pfad',), 'Welche MTU hat die Schnittstelle, und passt sie zum Netz?'),
    (('dienste',), 'Lauscht der Dienst überhaupt, und auf welcher Adresse (127.0.0.1 oder 0.0.0.0)?'),
    (('dienste',), 'Kommt eine Ablehnung (refused) oder gar keine Antwort (timed out)?'),
    (('dienste',), 'Läuft der Dienst laut systemctl?'),
    (('dienste',), 'Wird der richtige Port angesprochen?'),
)


def help_for(themes):
    return [(command, purpose) for scope, command, purpose in HELP if set(scope) & set(themes)]


def guide_for(themes):
    return [question for scope, question in GUIDE if set(scope) & set(themes)]


SOURCES = (
    'https://man7.org/linux/man-pages/man8/ip-address.8.html',
    'https://man7.org/linux/man-pages/man8/ip-route.8.html',
    'https://man7.org/linux/man-pages/man8/ip-neighbour.8.html',
    'https://man7.org/linux/man-pages/man8/ping.8.html',
    'https://man7.org/linux/man-pages/man8/arping.8.html',
    'https://man7.org/linux/man-pages/man5/resolv.conf.5.html',
    'https://man7.org/linux/man-pages/man1/getent.1.html',
    'https://bind9.readthedocs.io/en/latest/manpages.html#dig-dns-lookup-utility',
    'https://man7.org/linux/man-pages/man8/tracepath.8.html',
    'https://manpages.debian.org/trixie/traceroute/traceroute.1.en.html',
    'https://www.rfc-editor.org/rfc/rfc1191',
    'https://man7.org/linux/man-pages/man8/ss.8.html',
    'https://manpages.debian.org/trixie/netcat-openbsd/nc.1.en.html',
    'https://nmap.org/book/man-port-scanning-basics.html',
    'https://curl.se/docs/manpage.html',
)

# Short explanations of the terms each solution relies on (Musterlösung only).
GLOSSARY = {
    'Präfix': 'Die Zahl nach dem Schrägstrich, z. B. /24, gibt an, wie viele Bits der Adresse das Netz bezeichnen. /24 entspricht der Netzmaske 255.255.255.0 mit 254 nutzbaren Adressen, /28 nur 14.',
    'Direkt verbundenes Netz': 'Das Netz, in dem die eigene Adresse liegt. Geräte darin erreicht der Rechner ohne Router; in ip route steht es mit „proto kernel scope link“.',
    'Default-Route': 'Der Eintrag „default via …“: Weg für alle Ziele, für die keine genauere Route existiert, meist ins Internet.',
    'Gateway': 'Der Router, an den der Rechner Pakete für fremde Netze schickt. Er muss im direkt verbundenen Netz liegen.',
    'ARP': 'Address Resolution Protocol: ermittelt zur IPv4-Adresse im eigenen Netz die MAC-Adresse. ip neigh zeigt den ARP-Cache; FAILED heißt: keine Antwort.',
    'UP / LOWER_UP': 'UP: Schnittstelle ist eingeschaltet. LOWER_UP: Es besteht eine Verbindung (Kabel/Link). Fehlt UP, wurde die Schnittstelle abgeschaltet.',
    'Network is unreachable': 'Meldung des eigenen Rechners: Für dieses Ziel gibt es keine Route. Das Paket verlässt den Rechner nicht.',
    'Destination Host Unreachable': 'Der nächste Hop liegt im eigenen Netz, antwortet aber nicht auf ARP. Die Route existiert, das Ziel dahinter fehlt.',
    'Doppelte IP-Adresse': 'Zwei Geräte nutzen dieselbe IP. Das Gateway ordnet die Adresse abwechselnd der einen oder anderen MAC zu; Verbindungen brechen sporadisch ab.',
    'arping -D': 'Duplicate Address Detection: fragt per ARP, ob eine Adresse schon vergeben ist. Jede Antwort bedeutet einen Konflikt.',
    'Link-Local-Adresse': 'IPv6-Adresse fe80::/64, die jede Schnittstelle automatisch hat. Sie gilt nur im eigenen Segment und reicht nicht für Ziele außerhalb.',
    'ip route get': 'Fragt den Kernel, welche Route, welches Gateway und welche Schnittstelle er für ein bestimmtes Ziel wählen würde.',
    'DNS-Server (nameserver)': 'Server, der Namen in IP-Adressen übersetzt. Linux liest ihn aus /etc/resolv.conf, die NetworkManager erzeugt.',
    'Suchdomain': 'Die search-Zeile in /etc/resolv.conf. Ein Kurzname ohne Punkt (z. B. drucker) wird damit zu drucker.schule.example ergänzt.',
    'FQDN': 'Fully Qualified Domain Name: der vollständige Name mit allen Domainteilen, z. B. drucker.schule.example.',
    'NXDOMAIN': '„Non-Existent Domain“: Der DNS-Server antwortet, den Namen gibt es aber nicht. Die Namensauflösung selbst funktioniert.',
    'Timeout (DNS)': '„timed out“ oder „no servers could be reached“: Der befragte DNS-Server antwortet überhaupt nicht.',
    '/etc/hosts': 'Lokale Tabelle Name → Adresse. Sie wird vor DNS gefragt (Reihenfolge in /etc/nsswitch.conf) und läuft nie ab.',
    'TTL (DNS)': 'Time to Live eines DNS-Eintrags in Sekunden: so lange dürfen Caches die Antwort aufbewahren.',
    'dig gegenüber getent': 'dig fragt immer direkt den DNS-Server. getent, ping und Browser verwenden die Systemauflösung mit /etc/hosts.',
    'NetworkManager (nmcli)': 'Verwaltet die Netzwerkprofile unter Linux. nmcli connection modify ändert ein Profil dauerhaft, connection up wendet es an.',
    'Hop': 'Eine Zwischenstation (Router) auf dem Weg zum Ziel. traceroute zeigt jeden Hop mit seiner Antwortzeit.',
    'TTL (IP)': 'Time to Live im IP-Paket: jeder Router zieht 1 ab. Bei 0 verwirft er das Paket und meldet „Time to live exceeded“. traceroute nutzt das gezielt.',
    '* * * im traceroute': 'Von diesem Hop kam keine Antwort. Einzelne stumme Hops sind normal; antwortet ab dort niemand mehr, endet der Weg dort.',
    'Routing-Schleife': 'Zwei Router schicken sich ein Paket gegenseitig zu, weil ihre Routen aufeinander zeigen. Das Paket kreist, bis die TTL abläuft.',
    'MTU': 'Maximum Transmission Unit: größtes IP-Paket, das eine Schnittstelle oder Strecke ohne Fragmentierung transportiert; bei Ethernet 1500 Byte.',
    'DF-Bit': '„Don’t Fragment“: Das Paket darf unterwegs nicht zerteilt werden. Ist es zu groß, wird es verworfen. ping -M do setzt dieses Bit.',
    'Path MTU Discovery': 'Verfahren, mit dem ein Rechner die kleinste MTU auf dem Weg ermittelt. Es braucht die ICMP-Meldung „Fragmentation needed“ von Routern.',
    'PMTU-Blackhole': 'Eine Firewall filtert „Fragmentation needed“. Große Pakete verschwinden dann still, kleine kommen an; Webseiten bleiben hängen.',
    'MSS-Clamping': 'Die Firewall verkleinert die maximale TCP-Segmentgröße beim Verbindungsaufbau, damit Pakete auch bei kleinerer MTU passen.',
    'Paketgröße bei ping': 'ping -s gibt nur die Nutzdaten an. Dazu kommen 8 Byte ICMP- und 20 Byte IPv4-Kopf: -s 1472 ergibt ein 1500-Byte-Paket.',
    'Port': 'Nummer, über die ein Dienst auf einem Rechner erreichbar ist, z. B. 22 für SSH, 80 für HTTP, 443 für HTTPS.',
    'LISTEN': 'Zustand in ss: Ein Programm wartet auf diesem Port auf eingehende Verbindungen.',
    'Loopback (127.0.0.1)': 'Adresse, die nur den eigenen Rechner meint. Ein Dienst, der nur auf 127.0.0.1 lauscht, ist von anderen Rechnern aus nicht erreichbar.',
    '0.0.0.0 / [::]': 'In ss bedeutet das: Der Dienst lauscht auf allen Adressen des Rechners und ist damit auch von außen erreichbar.',
    'Connection refused': 'Der Zielrechner antwortet sofort mit einer Ablehnung (TCP-RST): Er ist erreichbar, aber auf diesem Port lauscht nichts.',
    'Timeout (TCP)': 'Es kommt gar keine Antwort. Meist verwirft eine Firewall die Anfrage still.',
    'open / closed / filtered': 'nmap-Zustände: open = Dienst antwortet, closed = Rechner lehnt ab (kein Dienst), filtered = keine Antwort, meist Firewall.',
    'Firewall': 'Regelwerk, das Pakete erlaubt oder verwirft. Unter Debian meist nftables; sudo nft list ruleset zeigt die Regeln.',
    'systemd-Dienst': 'Hintergrundprogramm, das systemd verwaltet. start startet es jetzt, enable startet es künftig beim Hochfahren.',
    'Echo Request / Reply': 'Die beiden ICMP-Nachrichten von ping. Anfrage und Antwort gehören über Kennung (id) und Sequenznummer (seq) zusammen.',
    'TCP-Handshake': 'Verbindungsaufbau in drei Schritten: SYN, SYN-ACK, ACK. Ein geschlossener Port antwortet auf SYN mit RST.',
    'Anzeigefilter': 'Filter über der Paketliste in Wireshark, z. B. arp || icmp. Er blendet nur aus, der Mitschnitt selbst bleibt vollständig.',
}


def texts(value):
    if (not isinstance(value, dict) or set(value) - set(TEXT_LIMITS)
            or not {'title', 'introduction', 'work', 'hints'} <= set(value)):
        raise ValueError('Titel, Einleitung, Arbeitsaufträge und Hinweise angeben.')
    for key, maximum in TEXT_LIMITS.items():
        text = value[key]
        if (not isinstance(text, str) or len(text) > maximum or '\x00' in text
                or any(ord(c) < 32 and c not in '\n\r\t' for c in text)):
            raise ValueError('Ungültiger oder zu langer Vorlagentext: ' + key)
        if set(re.findall(r'\{\{(.*?)\}\}', text)) - PLACEHOLDERS:
            raise ValueError('Unbekannter Platzhalter. Erlaubt: ' + ', '.join(sorted(PLACEHOLDERS)))
    if not value['title'].strip() or not plain_text(value['work']):
        raise ValueError('Titel und Arbeitsaufträge dürfen nicht leer sein.')
    return {key: value[key].strip() if key == 'title' else rich_html(value[key].strip()) for key in TEXT_LIMITS}


def options(value):
    if not isinstance(value, dict) or set(value) - set(DEFAULT_OPTIONS):
        raise ValueError('Unbekannte Aufgabeneinstellung.')
    result = dict(DEFAULT_OPTIONS, **value)
    if result['level'] not in LEVELS:
        raise ValueError('Ungültiges Niveau.')
    if type(result['teams']) is not int or not 1 <= result['teams'] <= 8:
        raise ValueError('Zwischen 1 und 8 Teams wählen.')
    for key in ('ipv6', 'part_a', 'part_b', 'part_c', 'wireshark'):
        if type(result[key]) is not bool:
            raise ValueError('IPv6, Aufgabenteile und Wireshark als Auswahl angeben.')
    if not (result['part_a'] or result['part_b'] or result['part_c']):
        raise ValueError('Mindestens einen Aufgabenteil (A, B oder C) auswählen.')
    if result['wireshark'] and not result['part_a']:
        raise ValueError('Der Wireshark-Mitschnitt gehört zu Teil B. Bitte Teil B mit auswählen.')
    themes = result['themes']
    if (not isinstance(themes, list) or not themes or len(set(themes)) != len(themes)
            or any(theme not in THEMES for theme in themes)):
        raise ValueError('Mindestens einen verfügbaren Themenbaustein auswählen.')
    result['themes'] = [theme for theme in THEMES if theme in themes]
    fault = result['fault']
    if fault != 'zufall':
        if fault not in FAULTS:
            raise ValueError('Unbekannter Fehler.')
        if FAULTS[fault]['theme'] not in result['themes']:
            raise ValueError('Der gewählte Fehler gehört nicht zu den ausgewählten Themen.')
        if FAULTS[fault].get('ipv6') and not result['ipv6']:
            raise ValueError('Dieser Fehler benötigt IPv6.')
    return result


def eligible(cfg):
    return [key for key, fault in FAULTS.items()
            if fault['theme'] in cfg['themes'] and (cfg['ipv6'] or not fault.get('ipv6'))]


# --- simulated host ---------------------------------------------------------

def mac_address(rng):
    return rng.choice(OUIS) + ':' + ':'.join('%02x' % rng.randrange(256) for _ in range(3))


def link_local(mac):
    octets = [int(part, 16) for part in mac.split(':')]
    octets[0] ^= 0x02
    eui = octets[:3] + [0xff, 0xfe] + octets[3:]
    return str(ip.IPv6Address('fe80::' + ':'.join('%x' % (eui[i] << 8 | eui[i+1]) for i in range(0, 8, 2)))) + '/64'


def healthy_host(rng, used, ipv6):
    school = allocate(SCHOOL_POOL, 24, rng, used)
    lab = allocate(LAB_POOL, 29, rng, used)
    partner = allocate(LAB_POOL, 29, rng, used)
    # Offsets below 200 keep printer (>= 240) and gateway (.1) outside a mistaken /28.
    offset = rng.randrange(20, 200)
    school_mac, lab_mac = mac_address(rng), '00:e0:4c:' + ':'.join('%02x' % rng.randrange(256) for _ in range(3))
    host = dict(
        name='labpc%02d' % rng.randrange(1, 40), user='schueler',
        school=dict(name='enp2s0', mac=school_mac, up=True,
                    v4=str(school.network_address + offset) + '/24',
                    gateway=str(school.network_address + 1)),
        lab=dict(name='enx' + lab_mac.replace(':', ''), mac=lab_mac, up=True,
                 v4=str(lab.network_address + 6) + '/29', gateway=str(lab.network_address + 1)),
        partner=dict(network=str(partner), host=str(partner.network_address + 6)),
        printer=str(school.network_address + rng.randrange(240, 250)),
        default=str(school.network_address + 1),
        partner_dev='lab',
        neighbors={},
    )
    host['domain'] = 'schule.example'
    host['intranet'] = str(school.network_address + rng.randrange(2, 10))
    host['intranet_old'] = str(school.network_address + rng.randrange(10, 16))
    host['dns_id'] = rng.randrange(1000, 65000)
    # Path towards the Internet: school router, firewall (TEST-NET-1), provider (TEST-NET-3).
    host['firewall'] = '192.0.2.' + str(rng.randrange(1, 60))
    host['provider'] = ['203.0.113.' + str(n) for n in sorted(rng.sample(range(1, 250), 3))]
    host['building'] = str(allocate(SCHOOL_POOL, 24, rng, used))
    host['core'] = str(allocate(SCHOOL_POOL, 30, rng, used).network_address + 2)
    host['gateway_mac'] = mac_address(rng)
    host['lab_gateway_mac'] = mac_address(rng)
    if ipv6:
        prefix = ip.ip_network((int(ip.ip_address('2001:db8::')) + (rng.randrange(1, 1 << 16) << 64), 64))
        host['school'].update(v6=str(prefix.network_address + offset) + '/64', gateway6=str(prefix.network_address + 1))
        host['default6'] = str(prefix.network_address + 1)
    return host


def school_net(host):
    return ip.ip_interface(host['school']['v4']).network


def render_addresses(host):
    lines = ['1: lo: <LOOPBACK,UP,LOWER_UP> mtu 65536 qdisc noqueue state UNKNOWN group default qlen 1000',
             '    link/loopback 00:00:00:00:00:00 brd 00:00:00:00:00:00',
             '    inet 127.0.0.1/8 scope host lo',
             '       valid_lft forever preferred_lft forever',
             '    inet6 ::1/128 scope host noprefixroute',
             '       valid_lft forever preferred_lft forever']
    for number, key in ((2, 'school'), (3, 'lab')):
        nic = host[key]
        flags = '<BROADCAST,MULTICAST,UP,LOWER_UP>' if nic['up'] else '<BROADCAST,MULTICAST>'
        state = 'UP' if nic['up'] else 'DOWN'
        lines += [f"{number}: {nic['name']}: {flags} mtu {nic.get('mtu', 1500)} qdisc fq_codel state {state} group default qlen 1000",
                  f"    link/ether {nic['mac']} brd ff:ff:ff:ff:ff:ff"]
        address = ip.ip_interface(nic['v4'])
        lines += [f"    inet {nic['v4']} brd {address.network.broadcast_address} scope global noprefixroute {nic['name']}",
                  '       valid_lft forever preferred_lft forever']
        if nic.get('v6') and nic['up']:
            lines += [f"    inet6 {nic['v6']} scope global noprefixroute",
                      '       valid_lft forever preferred_lft forever']
        if nic['up']:
            lines += [f"    inet6 {link_local(nic['mac'])} scope link noprefixroute",
                      '       valid_lft forever preferred_lft forever']
    return '\n'.join(lines)


def connected(host, key):
    nic = host[key]
    return ip.ip_interface(nic['v4']).network if nic['up'] else None


def routes4(host):
    """Kernel-consistent table: a gateway outside every connected net is not installed."""
    lines = []
    school = connected(host, 'school')
    lab = connected(host, 'lab')
    if host['default'] and school and ip.ip_address(host['default']) in school:
        lines.append(f"default via {host['default']} dev {host['school']['name']} proto static metric 100")
    if school:
        src = ip.ip_interface(host['school']['v4']).ip
        lines.append(f"{school} dev {host['school']['name']} proto kernel scope link src {src} metric 100")
    partner = host['partner']['network']
    if host['partner_dev'] == 'lab' and lab:
        lines.append(f"{partner} via {host['lab']['gateway']} dev {host['lab']['name']} proto static metric 101")
    elif host['partner_dev'] == 'school' and school:
        lines.append(f"{partner} via {host['school']['gateway']} dev {host['school']['name']} proto static metric 100")
    if lab:
        src = ip.ip_interface(host['lab']['v4']).ip
        lines.append(f"{lab} dev {host['lab']['name']} proto kernel scope link src {src} metric 101")
    return lines


def routes6(host):
    lines = []
    nic = host['school']
    if nic.get('v6') and nic['up']:
        lines.append(f"{ip.ip_interface(nic['v6']).network} dev {nic['name']} proto kernel metric 100 pref medium")
    for key in ('school', 'lab'):
        if host[key]['up']:
            lines.append(f"fe80::/64 dev {host[key]['name']} proto kernel metric {100 if key == 'school' else 101} pref medium")
    if nic.get('v6') and nic['up'] and host.get('default6'):
        lines.append(f"default via {host['default6']} dev {nic['name']} proto static metric 100 pref medium")
    return lines


def route_get(host, target):
    destination = ip.ip_address(target)
    source_of = lambda key: ip.ip_interface(host[key]['v4']).ip
    for key in ('school', 'lab'):
        net = connected(host, key)
        if net and destination in net:
            return f"{target} dev {host[key]['name']} src {source_of(key)} uid 1000\n    cache"
    for line in routes4(host):
        fields = line.split()
        if fields[0] != 'default' and '/' in fields[0] and 'via' in fields and destination in ip.ip_network(fields[0]):
            dev = fields[fields.index('dev')+1]
            key = 'school' if dev == host['school']['name'] else 'lab'
            return f"{target} via {fields[2]} dev {dev} src {source_of(key)} uid 1000\n    cache"
    for line in routes4(host):
        if line.startswith('default'):
            return f"{target} via {host['default']} dev {host['school']['name']} src {source_of('school')} uid 1000\n    cache"
    return 'RTNETLINK answers: Network is unreachable'


def neighbors(host):
    lines = []
    if connected(host, 'school'):
        gateway = host['default'] if host['default'] and ip.ip_address(host['default']) in school_net(host) else host['school']['gateway']
        if host.get('gateway_failed'):
            lines.append(f"{gateway} dev {host['school']['name']} FAILED")
        else:
            lines.append(f"{gateway} dev {host['school']['name']} lladdr {host['gateway_mac']} REACHABLE")
    if connected(host, 'lab'):
        lines.append(f"{host['lab']['gateway']} dev {host['lab']['name']} lladdr {host['lab_gateway_mac']} STALE")
    return '\n'.join(lines) if lines else ''


def ping(target, source, outcome, rng, count=3, family=4):
    size = '56(84)' if family == 4 else '56'
    head = f'PING {target} ({target}) {size} data bytes' if family == 6 else f'PING {target} ({target}) {size} bytes of data.'
    if outcome == 'network':
        return 'ping: connect: Network is unreachable'
    # Neighbours in the own network answer with TTL 64 below one millisecond.
    local = target not in (INTERNET4, INTERNET6)
    times = sorted(round(rng.uniform(0.3, 0.9), 2) if local else round(rng.uniform(8, 16), 1) for _ in range(count))
    ttl = 64 if local else 58
    if outcome == 'ok':
        lines = [head] + [f'64 bytes from {target}: icmp_seq={n} ttl={ttl} time={times[n-1]} ms' for n in range(1, count+1)]
        avg = round(sum(times)/count, 3 if local else 1)
        return '\n'.join(lines + ['', f'--- {target} ping statistics ---',
                                  f'{count} packets transmitted, {count} received, 0% packet loss, time {2000+rng.randrange(10)}ms',
                                  f'rtt min/avg/max/mdev = {times[0]}/{avg}/{times[-1]}/{round((times[-1]-times[0])/3, 3 if local else 1)} ms'])
    if outcome == 'host':
        lines = [head] + [f'From {source} icmp_seq={n} Destination Host Unreachable' for n in range(1, count+1)]
        return '\n'.join(lines + ['', f'--- {target} ping statistics ---',
                                  f'{count} packets transmitted, 0 received, +{count} errors, 100% packet loss, time {2040+rng.randrange(20)}ms',
                                  f'pipe {count}'])
    if outcome == 'silent':
        return '\n'.join([head, '', f'--- {target} ping statistics ---',
                          f'{count} packets transmitted, 0 received, 100% packet loss, time {2040+rng.randrange(20)}ms'])
    # 'flaky': every second answer is lost because a second device answers ARP.
    lines = [head]
    received = 0
    for n in range(1, count+1):
        if n % 2:
            lines.append(f'64 bytes from {target}: icmp_seq={n} ttl=64 time={round(rng.uniform(0.3, 0.9), 2)} ms')
            received += 1
    return '\n'.join(lines + ['', f'--- {target} ping statistics ---',
                              f'{count} packets transmitted, {received} received, {round(100-100*received/count)}% packet loss, time {5000+rng.randrange(30)}ms'])


def arping(host, conflict_mac=None):
    address = ip.ip_interface(host['school']['v4']).ip
    lines = [f"ARPING {address} from 0.0.0.0 {host['school']['name']}"]
    if conflict_mac:
        lines += [f'Unicast reply from {address} [{conflict_mac.upper()}]  1.204ms',
                  'Sent 1 probes (1 broadcast(s))', 'Received 1 response(s)']
    else:
        lines += ['Sent 3 probes (3 broadcast(s))', 'Received 0 response(s)']
    return '\n'.join(lines)


# --- faults -------------------------------------------------------------------

def out(command, text, evidence=(), noise=False):
    return dict(command=command, text=text, evidence=list(evidence), noise=noise)


def scenario(host, rng, key):
    """Apply one fault and render all outputs from the faulty state."""
    s, l = host['school'], host['lab']
    src = str(ip.ip_interface(s['v4']).ip)
    result = dict(outputs=[], fix=[], verify=[])
    if key == 'mask':
        address = ip.ip_interface(s['v4'])
        wrong = ip.ip_interface(f'{address.ip}/28')
        s['v4'] = str(wrong)
        result.update(
            symptom=f'Internet und der Netzwerkdrucker {host["printer"]} sind von {host["name"]} aus nicht erreichbar. Andere Rechner im Raum funktionieren.',
            outputs=[out(f'ping -c 3 {INTERNET4}', ping(INTERNET4, src, 'network', rng), ['Network is unreachable']),
                     out('ip address', render_addresses(host), [f'inet {wrong}']),
                     out('ip route', '\n'.join(routes4(host)), [f'{wrong.network} dev'])],
            cause=f'Falsches Präfix /28 statt /24 an {s["name"]}. Das eigene Netz ist dadurch nur {wrong.network}; '
                  f'das Gateway {s["gateway"]} und der Drucker liegen scheinbar außerhalb, deshalb fehlt die Default-Route.',
            evidence=f'ip address: inet {wrong} · ip route: nur {wrong.network}, keine Zeile „default via“.',
            fix=[f'sudo ip address del {wrong} dev {s["name"]}', f'sudo ip address add {address} dev {s["name"]}',
                 f'sudo ip route add default via {s["gateway"]} dev {s["name"]}'],
            verify=[f'ip route  →  default via {s["gateway"]} dev {s["name"]} …', f'ping -c 3 {INTERNET4}  →  0% packet loss'],
            explanation='Mit /28 umfasst das Netz nur 16 Adressen. Der Kernel installiert keine Default-Route über ein '
                        'Gateway, das nicht in einem direkt verbundenen Netz liegt. Dauerhaft wird die Korrektur im '
                        'NetworkManager-Profil gespeichert (nmcli connection modify … ipv4.addresses).')
    elif key == 'wrong_net':
        address = ip.ip_interface(s['v4'])
        octets = str(address.ip).split('.')
        third = octets[2]
        # A typical typing error: swapped digits, otherwise a neighbouring network.
        swapped = int(third[::-1]) if len(third) > 1 and third[::-1] != third and third[-1] != '0' and int(third[::-1]) < 256 else (int(third) + 10) % 256
        wrong = ip.ip_interface('.'.join(octets[:2] + [str(swapped), octets[3]]) + '/24')
        s['v4'] = str(wrong)
        result.update(
            symptom=f'{host["name"]} erreicht weder das Internet noch andere Rechner im Schulnetz. Das Netzwerkkabel ist laut Anzeige verbunden.',
            outputs=[out(f'ping -c 3 {s["gateway"]}', ping(s['gateway'], str(wrong.ip), 'network', rng), ['Network is unreachable']),
                     out('ip address', render_addresses(host), [f'inet {wrong}']),
                     out('ip route', '\n'.join(routes4(host)), [f'{wrong.network} dev'])],
            cause=f'Die Adresse {wrong} liegt im falschen Netz ({wrong.network}). Gateway {s["gateway"]} gehört zu {address.network}; '
                  'vermutlich ein Zahlendreher bei der manuellen Eingabe.',
            evidence=f'ip address: inet {wrong} · Gateway {s["gateway"]} passt nicht zum Netz {wrong.network}.',
            fix=[f'sudo ip address del {wrong} dev {s["name"]}', f'sudo ip address add {address} dev {s["name"]}',
                 f'sudo ip route add default via {s["gateway"]} dev {s["name"]}'],
            verify=[f'ping -c 3 {s["gateway"]}  →  Antworten vom Gateway', 'ip route  →  Zeile „default via …“'],
            explanation='Ein Gateway muss im selben Netz wie die eigene Adresse liegen. Liegt es außerhalb, kann der '
                        'Rechner es per ARP nicht ansprechen und keine Default-Route darüber eintragen.')
    elif key == 'duplicate':
        other = mac_address(rng)
        result.update(
            symptom=f'Auf {host["name"]} brechen Verbindungen immer wieder ab. Manchmal funktioniert alles, kurz darauf nichts mehr.',
            outputs=[out(f'ping -c 6 {s["gateway"]}', ping(s['gateway'], src, 'flaky', rng, count=6), ['packet loss']),
                     out('ip address', render_addresses(host), [f'link/ether {s["mac"]}', f'inet {s["v4"]}']),
                     out(f'sudo arping -D -I {s["name"]} -c 3 {src}', arping(host, other), ['Unicast reply from'])],
            cause=f'Doppelte IP-Adresse: Ein anderes Gerät mit der MAC-Adresse {other.upper()} verwendet ebenfalls {src}.',
            evidence=f'arping -D erhält eine Antwort für die eigene Adresse {src} von einer fremden MAC-Adresse '
                     f'(eigene MAC laut ip address: {s["mac"]}).',
            fix=['Das zweite Gerät über seine MAC-Adresse ermitteln (z. B. Switch-MAC-Tabelle) und umkonfigurieren',
                 f'oder diesem Rechner eine freie Adresse geben: sudo ip address replace <freie Adresse>/24 dev {s["name"]}'],
            verify=[f'sudo arping -D -I {s["name"]} -c 3 <Adresse>  →  Received 0 response(s)',
                    f'ping -c 6 {s["gateway"]}  →  0% packet loss'],
            explanation='Bei doppelten Adressen gewinnt abwechselnd das eine oder das andere Gerät die ARP-Zuordnung beim '
                        'Gateway. Darum gehen nur einzelne Pakete verloren. arping -D (Duplicate Address Detection) '
                        'macht den Konflikt sichtbar.')
    elif key == 'down':
        s['up'] = False
        result.update(
            symptom=f'Seit der letzten Unterrichtsstunde hat {host["name"]} kein Internet mehr. Das Labornetz zum Partnerplatz funktioniert weiterhin.',
            outputs=[out(f'ping -c 3 {INTERNET4}', ping(INTERNET4, src, 'network', rng), ['Network is unreachable']),
                     out('ip address', render_addresses(host), [f'{s["name"]}: <BROADCAST,MULTICAST> mtu 1500 qdisc fq_codel state DOWN']),
                     out('ip route', '\n'.join(routes4(host)), [])],
            cause=f'Die Schnittstelle {s["name"]} ist administrativ abgeschaltet (kein UP-Flag, state DOWN). '
                  'Dadurch fehlen ihr Netz und die Default-Route in der Routingtabelle.',
            evidence=f'ip address: {s["name"]} ohne UP/LOWER_UP, state DOWN · ip route: nur noch das Labornetz.',
            fix=[f'sudo ip link set {s["name"]} up', f'(dauerhaft über NetworkManager: nmcli connection up <Profil>)'],
            verify=[f'ip address show {s["name"]}  →  <BROADCAST,MULTICAST,UP,LOWER_UP> … state UP',
                    f'ip route  →  default via {s["gateway"]} dev {s["name"]} …'],
            explanation='Eine abgeschaltete Schnittstelle behält ihre Adresse, aber der Kernel entfernt ihre Routen. '
                        'Fehlte stattdessen nur das Kabel, stünde UP ohne LOWER_UP und NO-CARRIER in der Ausgabe.')
    elif key == 'ipv6_missing':
        address = s.pop('v6')
        result.update(
            symptom=f'Ein IPv6-Dienst unter {INTERNET6} ist von {host["name"]} aus nicht erreichbar. IPv4 funktioniert normal.',
            outputs=[out(f'ping -6 -c 3 {INTERNET6}', ping(INTERNET6, src, 'network', rng, family=6), ['Network is unreachable']),
                     out('ip -6 address show dev ' + s['name'], ipv6_only(host), ['scope link']),
                     out('ip -6 route', '\n'.join(routes6(host)), ['fe80::/64'])],
            cause=f'An {s["name"]} gibt es nur die Link-Local-Adresse (fe80::…). Es fehlen die globale IPv6-Adresse '
                  f'{address} und damit auch die IPv6-Default-Route.',
            evidence='ip -6 address: nur „scope link“ · ip -6 route: nur fe80::/64, keine Zeile „default via“.',
            fix=[f'sudo ip -6 address add {address} dev {s["name"]}',
                 f'sudo ip -6 route add default via {s["gateway6"]} dev {s["name"]}'],
            verify=[f'ip -6 route  →  default via {s["gateway6"]} …', f'ping -6 -c 3 {INTERNET6}  →  Antworten'],
            explanation='Link-Local-Adressen gelten nur im eigenen Segment. Für Ziele außerhalb braucht der Rechner eine '
                        'globale Adresse und eine IPv6-Default-Route (statisch, per SLAAC oder DHCPv6).')
    elif key == 'no_default':
        host['default'] = None
        result.update(
            symptom=f'{host["name"]} erreicht den Drucker {host["printer"]} im Schulnetz, aber keine Seite im Internet.',
            outputs=[out(f'ping -c 3 {host["printer"]}', ping(host['printer'], src, 'ok', rng), ['0% packet loss']),
                     out(f'ping -c 3 {INTERNET4}', ping(INTERNET4, src, 'network', rng), ['Network is unreachable']),
                     out('ip route', '\n'.join(routes4(host)), [])],
            cause='Die Default-Route fehlt. Ziele im eigenen Netz funktionieren, für alle anderen Netze kennt der Rechner keinen Weg.',
            evidence='ip route: keine Zeile „default via …“ · ping ins Internet: Network is unreachable.',
            fix=[f'sudo ip route add default via {s["gateway"]} dev {s["name"]}'],
            verify=[f'ip route  →  default via {s["gateway"]} dev {s["name"]} …', f'ping -c 3 {INTERNET4}  →  0% packet loss'],
            explanation='„Network is unreachable“ meldet der eigene Rechner sofort, weil keine passende Route existiert. '
                        'Das Paket verlässt den Rechner gar nicht.')
    elif key == 'wrong_gateway':
        net = school_net(host)
        wrong = str(net.network_address + 254)
        host['default'] = wrong
        host['gateway_failed'] = True
        result.update(
            symptom=f'{host["name"]} erreicht Rechner im eigenen Schulnetz, aber keine Seite im Internet.',
            outputs=[out(f'ping -c 3 {INTERNET4}', ping(INTERNET4, src, 'host', rng), ['Destination Host Unreachable']),
                     out('ip route', '\n'.join(routes4(host)), [f'default via {wrong}']),
                     out('ip neigh', neighbors(host), [f'{wrong} dev {s["name"]} FAILED'])],
            cause=f'Falsches Gateway: Die Default-Route zeigt auf {wrong}. Dort antwortet kein Gerät; der Router ist {s["gateway"]}.',
            evidence=f'ip route: default via {wrong} · ip neigh: {wrong} FAILED (keine ARP-Antwort).',
            fix=[f'sudo ip route replace default via {s["gateway"]} dev {s["name"]}'],
            verify=[f'ip neigh  →  {s["gateway"]} … REACHABLE', f'ping -c 3 {INTERNET4}  →  0% packet loss'],
            explanation='„Destination Host Unreachable“ von der eigenen Adresse bedeutet: Der nächste Hop liegt im eigenen '
                        'Netz, antwortet aber nicht auf ARP. Die Route existiert also, zeigt aber auf das falsche Ziel.')
    elif key == 'wrong_interface':
        host['partner_dev'] = 'school'
        partner = host['partner']['host']
        result.update(
            symptom=f'Der Partnerrechner {partner} im Labornetz antwortet nicht. Internet und Schulnetz funktionieren auf {host["name"]}.',
            outputs=[out(f'ping -c 3 {partner}', ping(partner, src, 'silent', rng), ['100% packet loss']),
                     out('ip route', '\n'.join(routes4(host)), [f'{host["partner"]["network"]} via {s["gateway"]} dev {s["name"]}']),
                     out(f'ip route get {partner}', route_get(host, partner), [f'via {s["gateway"]} dev {s["name"]}'])],
            cause=f'Die Route ins Partnernetz {host["partner"]["network"]} zeigt über das Schulnetz ({s["name"]}, {s["gateway"]}) '
                  f'statt über den USB-Laboradapter {l["name"]} zum Laborrouter {l["gateway"]}.',
            evidence=f'ip route get {partner}: via {s["gateway"]} dev {s["name"]}.',
            fix=[f'sudo ip route del {host["partner"]["network"]} via {s["gateway"]} dev {s["name"]}',
                 f'sudo ip route add {host["partner"]["network"]} via {l["gateway"]} dev {l["name"]}'],
            verify=[f'ip route get {partner}  →  via {l["gateway"]} dev {l["name"]}', f'ping -c 3 {partner}  →  Antworten'],
            explanation='Die Pakete gehen ohne Fehlermeldung ins Schulnetz und kommen dort nie an. ip route get zeigt '
                        'direkt, welche Route der Kernel für ein Ziel wählt.')
    elif key.startswith('dns_'):
        result.update(dns_scenario(host, rng, key, src))
    elif key.startswith('path_'):
        result.update(path_scenario(host, rng, key, src))
    elif key.startswith('svc_'):
        result.update(service_scenario(host, rng, key, src))
    else:
        raise ValueError('Unbekannter Fehler.')
    return result


def ipv6_only(host):
    s = host['school']
    lines = [f"2: {s['name']}: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 state UP qlen 1000"]
    if s.get('v6'):
        lines += [f"    inet6 {s['v6']} scope global noprefixroute", '       valid_lft forever preferred_lft forever']
    lines += [f"    inet6 {link_local(s['mac'])} scope link noprefixroute", '       valid_lft forever preferred_lft forever']
    return '\n'.join(lines)



# --- DNS ----------------------------------------------------------------------
DIG_VERSION = '9.20.15-1~deb13u1-Debian'
WHEN = 'Tue Nov 24 10:15:02 CET 2026'


def resolv_conf(search, server):
    return '# Generated by NetworkManager\nsearch ' + search + '\nnameserver ' + server


def dig(name, server, rng, status='NOERROR', answers=(), soa=None, timeout=False, kind='A'):
    if timeout:
        lines = [f';; communications error to {server}#53: timed out'] * 3
        return '\n'.join(lines + ['', f'; <<>> DiG {DIG_VERSION} <<>> {name}', ';; global options: +cmd',
                                   ';; no servers could be reached'])
    qid = rng.randrange(1000, 65000)
    flags = 'qr aa rd ra' if soa else 'qr rd ra'
    lines = [f'; <<>> DiG {DIG_VERSION} <<>> {name}', ';; global options: +cmd', ';; Got answer:',
             f';; ->>HEADER<<- opcode: QUERY, status: {status}, id: {qid}',
             f';; flags: {flags}; QUERY: 1, ANSWER: {len(answers)}, AUTHORITY: {1 if soa else 0}, ADDITIONAL: 1', '',
             ';; OPT PSEUDOSECTION:', '; EDNS: version: 0, flags:; udp: 1232', ';; QUESTION SECTION:',
             f';{name}.\t\t\tIN\t{kind}', '']
    if answers:
        lines += [';; ANSWER SECTION:'] + [f'{name}.\t\t{ttl}\tIN\t{kind}\t{value}' for ttl, value in answers] + ['']
    if soa:
        lines += [';; AUTHORITY SECTION:', f'{soa}.\t\t3600\tIN\tSOA\tns1.{soa}. hostmaster.{soa}. 2026112401 10800 3600 604800 3600', '']
    lines += [f';; Query time: {rng.randrange(1, 30)} msec', f';; SERVER: {server}#53({server}) (UDP)', f';; WHEN: {WHEN}',
              f';; MSG SIZE  rcvd: {rng.randrange(56, 110)}']
    return '\n'.join(line.expandtabs(8) for line in lines)


def nslookup(name, server, address=None):
    head = f'Server:         {server}\nAddress:        {server}#53\n\n'
    if address is None:
        return head + f"** server can't find {name}: NXDOMAIN"
    return head + f'Non-authoritative answer:\nName:   {name}\nAddress: {address}'


def ping_name(name, address, source, outcome, rng):
    if outcome == 'temporary':
        return f'ping: {name}: Temporary failure in name resolution'
    if outcome == 'unknown':
        return f'ping: {name}: Name or service not known'
    text = ping(address, source, outcome, rng)
    return text.replace(f'PING {address} ({address})', f'PING {name} ({address})', 1)


def dns_scenario(host, rng, key, src):
    s = host['school']
    server, domain = s['gateway'], host['domain']
    web = 'www.example.org'
    if key == 'dns_wrong_resolver':
        wrong = str(school_net(host).network_address + 53)
        return dict(
            symptom=f'Auf {host["name"]} öffnet der Browser keine Webseiten mehr. Ein Kollege sagt: „Mit der IP-Adresse geht es aber.“',
            outputs=[out(f'ping -c 3 {web}', ping_name(web, INTERNET4, src, 'temporary', rng), ['Temporary failure in name resolution']),
                     out(f'ping -c 3 {INTERNET4}', ping(INTERNET4, src, 'ok', rng), []),
                     out('cat /etc/resolv.conf', resolv_conf(domain, wrong), [f'nameserver {wrong}']),
                     out(f'dig {web}', dig(web, wrong, rng, timeout=True), ['timed out', 'no servers could be reached'])],
            cause=f'Falscher DNS-Server: Eingetragen ist {wrong}, dort antwortet kein DNS-Dienst. Der DNS-Server des Schulnetzes ist {server}.',
            evidence=f'resolv.conf: nameserver {wrong} · dig: timed out, no servers could be reached · ping per IP funktioniert.',
            fix=[f'sudo nmcli connection modify LAN-Schule ipv4.dns {server}', 'sudo nmcli connection up LAN-Schule'],
            verify=[f'cat /etc/resolv.conf  →  nameserver {server}', f'dig {web}  →  status: NOERROR, ANSWER SECTION mit {INTERNET4}'],
            explanation='Wenn eine IP-Adresse erreichbar ist, der Name aber nicht, liegt das Problem bei der Namensauflösung. '
                        '„Timed out“ heißt: Der befragte Server antwortet gar nicht. Ein erreichbarer Server, der den Namen nicht '
                        'kennt, würde dagegen NXDOMAIN melden.')
    if key == 'dns_search_typo':
        wrong = 'schlue.example'
        short = 'drucker'
        printer = host['printer']
        return dict(
            symptom=f'Auf {host["name"]} funktioniert der Kurzname „{short}“ nicht mehr. Auf den anderen Rechnern im Raum klappt „ping {short}“.',
            outputs=[out(f'ping -c 3 {short}', ping_name(short, printer, src, 'unknown', rng), ['Name or service not known']),
                     out(f'ping -c 3 {short}.{domain}', ping_name(f'{short}.{domain}', printer, src, 'ok', rng), []),
                     out('cat /etc/resolv.conf', resolv_conf(wrong, server), [f'search {wrong}'])],
            cause=f'Tippfehler in der Suchdomain: „{wrong}“ statt „{domain}“. Der Kurzname wird deshalb zu {short}.{wrong} ergänzt, '
                  'und diesen Namen gibt es nicht.',
            evidence=f'resolv.conf: search {wrong} · vollständiger Name {short}.{domain} funktioniert.',
            fix=[f'sudo nmcli connection modify LAN-Schule ipv4.dns-search {domain}', 'sudo nmcli connection up LAN-Schule'],
            verify=[f'cat /etc/resolv.conf  →  search {domain}', f'ping -c 3 {short}  →  Antworten von {printer}'],
            explanation='Kurznamen ohne Punkt ergänzt der Resolver mit den Domains aus der search-Zeile. Der DNS-Server selbst '
                        'arbeitet korrekt; das zeigt der funktionierende vollständige Name (FQDN).')
    if key == 'dns_nxdomain':
        right, wrong = f'moodle.{domain}', f'moodel.{domain}'
        return dict(
            symptom=f'Ein Schüler meldet: „{wrong} ist kaputt.“ Andere Webseiten funktionieren auf {host["name"]}.',
            outputs=[out(f'ping -c 3 {wrong}', ping_name(wrong, '', src, 'unknown', rng), ['Name or service not known']),
                     out(f'nslookup {wrong}', nslookup(wrong, server), ['NXDOMAIN']),
                     out(f'dig {wrong}', dig(wrong, server, rng, status='NXDOMAIN', soa=domain), ['status: NXDOMAIN', f'SERVER: {server}#53'])],
            cause=f'Der DNS-Server {server} antwortet korrekt, aber den Namen {wrong} gibt es nicht (Tippfehler, richtig: {right}).',
            evidence=f'dig: status: NXDOMAIN von SERVER {server} · nslookup: server can\'t find … NXDOMAIN.',
            fix=[f'Richtigen Namen verwenden: {right}', f'Prüfen: dig +short {right}'],
            verify=[f'dig {right}  →  status: NOERROR mit A-Eintrag', f'ping -c 3 {right}  →  Antworten'],
            explanation='NXDOMAIN ist eine echte Antwort des DNS-Servers: „Diesen Namen gibt es nicht.“ Die Namensauflösung '
                        'funktioniert also. Bei einem Timeout dagegen antwortet gar kein Server. Diese Unterscheidung spart viel Suche.')
    if key == 'dns_stale_hosts':
        name = f'intranet.{domain}'
        old, new = host['intranet_old'], host['intranet']
        ttl = rng.choice((300, 600, 900, 3600))
        return dict(
            symptom=f'Seit dem Serverumzug am Wochenende erreicht {host["name"]} das Intranet {name} nicht mehr. Bei allen anderen Rechnern geht es.',
            outputs=[out(f'ping -c 3 {name}', ping_name(name, old, src, 'host', rng), [f'({old})', 'Destination Host Unreachable']),
                     out(f'dig {name}', dig(name, server, rng, answers=[(ttl, new)]), [f'IN      A       {new}']),
                     out(f'getent hosts {name}', f'{old}{" " * max(1, 16-len(old))}{name}', [old]),
                     out('grep intranet /etc/hosts', f'{old}\t{name} intranet'.expandtabs(8), [old])],
            cause=f'Veralteter Eintrag in /etc/hosts: {name} zeigt dort noch auf die alte Adresse {old}. '
                  f'Der DNS-Server liefert bereits die neue Adresse {new}, wird aber gar nicht gefragt, weil /etc/hosts Vorrang hat.',
            evidence=f'dig: A {new} · getent hosts und ping: {old} · /etc/hosts enthält {old}.',
            fix=[f"sudo sed -i '/{name.replace('.', '[.]')}/d' /etc/hosts"],
            verify=[f'getent hosts {name}  →  {new}', f'ping -c 3 {name}  →  Antworten von {new}'],
            explanation=f'dig fragt immer direkt den DNS-Server und ignoriert /etc/hosts. Programme wie ping oder der Browser '
                        f'nutzen dagegen die Reihenfolge aus /etc/nsswitch.conf (files vor dns). Die TTL von {ttl} s gilt für '
                        'DNS-Caches, ein Eintrag in /etc/hosts läuft dagegen nie ab.')
    raise ValueError('Unbekannter Fehler.')


# --- Pfad & MTU ---------------------------------------------------------------

def traceroute(target, hops, rng, max_hops=8):
    """hops: list of addresses; None for a silent hop. Ends at the target when it is reached."""
    lines = [f'traceroute to {target} ({target}), {max_hops} hops max, 60 byte packets']
    delay = 0.15
    for number, address in enumerate(hops[:max_hops], 1):
        if address is None:
            lines.append(f'{number:2d}  * * *')
            continue
        # Local hops stay in the sub-millisecond to low millisecond range; the provider adds WAN latency.
        delay = delay + rng.uniform(0.25, 0.8) if address.startswith(('172.', '192.0.2.')) else max(delay, 8) + rng.uniform(0.5, 3)
        times = '  '.join(f'{delay + rng.uniform(-0.15, 0.15):.3f} ms' for _ in range(3))
        lines.append(f'{number:2d}  {address}  {times}')
    return '\n'.join(lines)


def ip_link(nic):
    flags = '<BROADCAST,MULTICAST,UP,LOWER_UP>' if nic['up'] else '<BROADCAST,MULTICAST>'
    return (f"2: {nic['name']}: {flags} mtu {nic.get('mtu', 1500)} qdisc fq_codel state {'UP' if nic['up'] else 'DOWN'} "
            f"mode DEFAULT group default qlen 1000\n    link/ether {nic['mac']} brd ff:ff:ff:ff:ff:ff")


def ping_size(target, source, size, outcome, rng):
    text = ping(target, source, outcome, rng)
    text = text.replace(f'({target}) 56(84) bytes of data.', f'({target}) {size}({size + 28}) bytes of data.', 1)
    return text.replace('64 bytes from', f'{size + 8} bytes from')


def path_scenario(host, rng, key, src):
    s = host['school']
    gw, fw, provider = s['gateway'], host['firewall'], host['provider']
    route = [gw, fw, *provider, INTERNET4]
    if key == 'path_upstream':
        return dict(
            symptom=f'Seit 10 Uhr hat im ganzen Haus kein Rechner Internet. Drucker und Moodle im Schulnetz funktionieren, auch auf {host["name"]}.',
            outputs=[out(f'ping -c 3 {INTERNET4}', ping(INTERNET4, src, 'silent', rng), ['100% packet loss']),
                     out(f'ping -c 3 {gw}', ping(gw, src, 'ok', rng), []),
                     out(f'traceroute -n -m 8 {INTERNET4}', traceroute(INTERNET4, [gw, fw] + [None] * 6, rng), [f' {fw} ', ' 3  * * *'])],
            cause=f'Der Weg endet hinter der Firewall {fw} (Hop 2): Ab Hop 3 antwortet niemand mehr. Der Rechner, seine Route '
                  'und das Schulnetz sind in Ordnung; die Störung liegt beim Übergang zum Provider.',
            evidence=f'traceroute: Hop 1 {gw} und Hop 2 {fw} antworten, ab Hop 3 nur * * * · ping zum Gateway funktioniert.',
            fix=['Am Rechner nichts ändern', 'Störung mit traceroute-Ausgabe an Netzwerkadministration bzw. Provider melden',
                 'Dort: WAN-Anschluss und Leitung der Firewall prüfen'],
            verify=[f'traceroute -n {INTERNET4}  →  Ziel wird nach wenigen Hops erreicht', f'ping -c 3 {INTERNET4}  →  0% packet loss'],
            explanation='Wenn alle Rechner betroffen sind und traceroute bis ins eigene Netz bzw. bis zur Firewall kommt, '
                        'ist die Ursache außerhalb des eigenen Rechners. Eine gute Fehlersuche erkennt auch: „Hier kann ich '
                        'nichts reparieren, aber ich kann den Fehler genau eingrenzen.“')
    if key == 'path_loop':
        building = ip.ip_network(host['building'])
        target = str(building.network_address + rng.randrange(10, 200))
        core = host['core']
        back = str(ip.ip_address(core) - 1)
        hops = [gw] + [core if n % 2 == 0 else back for n in range(7)]
        return dict(
            symptom=f'Rechner im Netz {building} (Nachbargebäude) sind von {host["name"]} aus nicht erreichbar. Internet funktioniert.',
            outputs=[out(f'ping -c 3 {target}', '\n'.join([f'PING {target} ({target}) 56(84) bytes of data.'] +
                         [f'From {core} icmp_seq={n} Time to live exceeded' for n in (1, 2, 3)] +
                         ['', f'--- {target} ping statistics ---',
                          f'3 packets transmitted, 0 received, +3 errors, 100% packet loss, time {2030 + rng.randrange(30)}ms']),
                         ['Time to live exceeded']),
                     out(f'traceroute -n -m 8 {target}', traceroute(target, hops, rng), [f' {core} ', f' {back} '])],
            cause=f'Routing-Schleife: Router {core} und {back} schicken sich die Pakete für {building} gegenseitig zu, '
                  'bis die TTL abläuft. Auf einem der beiden Router zeigt die Route für dieses Netz in die falsche Richtung.',
            evidence=f'traceroute: {core} und {back} wechseln sich ab Hop 2 ab · ping: Time to live exceeded.',
            fix=['Am Rechner nichts ändern',
                 f'Auf Router {core}: Route für {building} prüfen und auf den richtigen nächsten Router setzen (Netzwerkadministration)'],
            verify=[f'traceroute -n {target}  →  keine wiederholten Adressen, Ziel wird erreicht', f'ping -c 3 {target}  →  Antworten'],
            explanation='Jeder Router verringert die TTL um 1. In einer Schleife kreist das Paket, bis die TTL 0 erreicht; der '
                        'letzte Router meldet dann „Time to live exceeded“. Die sich wiederholenden Adressen im traceroute '
                        'zeigen genau die beiden beteiligten Router.')
    if key == 'path_blackhole':
        mtu = rng.choice((1492, 1480, 1460))
        ok_size, bad_size = mtu - 28, 1472
        return dict(
            symptom=f'Auf {host["name"]} laden manche Webseiten nur halb und bleiben dann weiß. ping auf den Server klappt aber.',
            outputs=[out(f'ping -c 3 {INTERNET4}', ping(INTERNET4, src, 'ok', rng), []),
                     out(f'ping -c 3 -M do -s {bad_size} {INTERNET4}', ping_size(INTERNET4, src, bad_size, 'silent', rng), ['100% packet loss']),
                     out(f'ping -c 3 -M do -s {ok_size} {INTERNET4}', ping_size(INTERNET4, src, ok_size, 'ok', rng), ['0% packet loss']),
                     out(f'ip link show {s["name"]}', ip_link(s), ['mtu 1500'])],
            cause=f'PMTU-Blackhole: Auf dem Weg ins Internet passen nur Pakete bis {mtu} Byte (z. B. PPPoE/VPN beim Provider). '
                  'Die Meldung „Fragmentation needed“ wird unterwegs gefiltert, deshalb verschwinden größere Pakete ohne Fehlermeldung.',
            evidence=f'-s {bad_size} (= {bad_size + 28} Byte) geht verloren, -s {ok_size} (= {mtu} Byte) kommt an · lokale MTU 1500.',
            fix=[f'Netzwerkadministration: MSS-Clamping an der Firewall aktivieren oder ICMP „Fragmentation needed“ erlauben',
                 f'Notlösung nur zum Test: sudo ip link set {s["name"]} mtu {mtu}'],
            verify=[f'ping -c 3 -M do -s {ok_size} {INTERNET4}  →  0% packet loss', 'Webseiten laden vollständig'],
            explanation=f'Kleine Pakete wie ein normaler ping (84 Byte) passen immer. Große Webseiten-Pakete (1500 Byte) sind '
                        f'zu groß für die Strecke mit MTU {mtu}. Mit gesetztem DF-Bit und schrittweise kleinerem -s findet man '
                        'die Grenze: Nutzdaten + 28 Byte = MTU der Strecke.')
    if key == 'path_local_mtu':
        s['mtu'] = 9000
        return dict(
            symptom=f'Auf {host["name"]} bricht der Upload größerer Dateien ins Moodle ab, und eine SSH-Anmeldung hängt nach dem Passwort. Kleine Abfragen funktionieren.',
            outputs=[out(f'ping -c 3 {gw}', ping(gw, src, 'ok', rng), []),
                     out(f'ping -c 3 -s 2000 {gw}', ping_size(gw, src, 2000, 'silent', rng), ['100% packet loss']),
                     out(f'ip link show {s["name"]}', ip_link(s), ['mtu 9000'])],
            cause=f'Falsche MTU: An {s["name"]} ist mtu 9000 (Jumbo Frames) eingestellt, das Schulnetz arbeitet mit 1500. '
                  'Der Switch verwirft alle größeren Rahmen.',
            evidence=f'ip link: {s["name"]} … mtu 9000 · kleiner ping geht, ping mit -s 2000 bekommt keine Antwort.',
            fix=[f'sudo ip link set {s["name"]} mtu 1500', 'dauerhaft: sudo nmcli connection modify LAN-Schule 802-3-ethernet.mtu 1500'],
            verify=[f'ip link show {s["name"]}  →  mtu 1500', f'ping -c 3 -s 1472 -M do {gw}  →  0% packet loss'],
            explanation='Die MTU muss auf allen Geräten eines Netzsegments gleich sein. Sendet der Rechner größere Rahmen, als '
                        'Switch und Gegenstelle annehmen, gehen gerade die großen Pakete einer Verbindung verloren, kleine Pakete '
                        'wie ping oder DNS funktionieren weiter.')
    raise ValueError('Unbekannter Fehler.')


# --- Ports & Dienste ----------------------------------------------------------
SERVICE_NAMES = {22: 'ssh', 80: 'http', 443: 'https', 8080: 'http-proxy'}


def ss_listen(rows):
    """rows: (address, port, process or None); always includes the LAB SSH service."""
    lines = ['State  Recv-Q Send-Q Local Address:Port  Peer Address:PortProcess']
    for address, port, process in [('0.0.0.0', 22, None), *rows, ('[::]', 22, None)]:
        local = f'{address}:{port}'
        peer = '[::]:*' if address.startswith('[') else '0.0.0.0:*'
        users = f'users:(("{process[0]}",pid={process[1]},fd=3))' if process else ''
        lines.append(f'LISTEN 0      {128 if port == 22 else 511:<6} {local:>18} {peer:>12}    {users}'.rstrip())
    return '\n'.join(lines)


def nmap(target, mac, ports, rng):
    lines = [f'Starting Nmap 7.95 ( https://nmap.org ) at 2026-11-24 10:15 CET', f'Nmap scan report for {target}',
             f'Host is up (0.00{rng.randrange(30, 90)}s latency).', '', 'PORT     STATE    SERVICE']
    for port, state in ports:
        lines.append(f'{str(port) + "/tcp":<8} {state:<8} {SERVICE_NAMES.get(port, "unknown")}')
    lines += [f'MAC Address: {mac.upper()} (Realtek Semiconductor)', '',
              f'Nmap done: 1 IP address (1 host up) scanned in {rng.uniform(1.1, 3.4):.2f} seconds']
    return '\n'.join(lines)


def service_scenario(host, rng, key, src):
    lab = host['lab']
    own = str(ip.ip_interface(lab['v4']).ip)
    # The partner seat shares the lab segment, so nmap can report its MAC address and TTL stays 64.
    partner = str(ip.ip_interface(lab['v4']).network.network_address + 5)
    partner_mac = '00:e0:4c:' + ':'.join('%02x' % rng.randrange(256) for _ in range(3))
    pid = rng.randrange(1200, 9000)
    if key == 'svc_localhost':
        return dict(
            symptom=f'Das Partnerteam soll Ihre Testseite auf {host["name"]} (Port 8080) öffnen. Bei ihm kommt „Verbindung abgelehnt“; auf {host["name"]} selbst funktioniert die Seite.',
            outputs=[out('curl -I http://127.0.0.1:8080', 'HTTP/1.0 200 OK\nServer: SimpleHTTP/0.6 Python/3.13.5\n'
                         'Date: Tue, 24 Nov 2026 09:15:02 GMT\nContent-type: text/html; charset=utf-8\nContent-Length: 412', []),
                     out(f'curl -I http://{own}:8080', f'curl: (7) Failed to connect to {own} port 8080 after 0 ms: Couldn\'t connect to server',
                         ['Failed to connect']),
                     out('ss -tlnp', ss_listen([('127.0.0.1', 8080, ('python3', pid))]), ['127.0.0.1:8080'])],
            cause='Der Webdienst lauscht nur auf 127.0.0.1 (Loopback). Er ist dadurch nur vom eigenen Rechner aus erreichbar, '
                  f'nicht über die Laboradresse {own}.',
            evidence='ss: 127.0.0.1:8080 · curl auf 127.0.0.1 klappt, auf die Laboradresse kommt „Couldn’t connect“.',
            fix=[f'Dienst beenden und auf allen Adressen starten: python3 -m http.server 8080 --bind 0.0.0.0',
                 f'oder gezielt nur im Labornetz: python3 -m http.server 8080 --bind {own}'],
            verify=['ss -tlnp  →  0.0.0.0:8080 bzw. ' + own + ':8080', f'curl -I http://{own}:8080  →  HTTP/1.0 200 OK'],
            explanation='Die Adresse in der Spalte „Local Address“ entscheidet, wer einen Dienst erreicht: 127.0.0.1 nur der '
                        'eigene Rechner, 0.0.0.0 alle. Viele Entwicklungsserver binden aus Sicherheitsgründen standardmäßig nur an Loopback.')
    if key == 'svc_not_running':
        return dict(
            symptom=f'Auf {host["name"]} soll der Apache-Webserver laufen. Das Partnerteam und auch der Browser auf {host["name"]} melden „Verbindung abgelehnt“.',
            outputs=[out('curl -I http://localhost', 'curl: (7) Failed to connect to localhost port 80 after 0 ms: Couldn\'t connect to server',
                         ['Failed to connect']),
                     out('ss -tlnp', ss_listen([]), []),
                     out('systemctl status apache2', '○ apache2.service - The Apache HTTP Server\n'
                         '     Loaded: loaded (/usr/lib/systemd/system/apache2.service; disabled; preset: enabled)\n'
                         '     Active: inactive (dead)\n       Docs: https://httpd.apache.org/docs/2.4/', ['inactive (dead)', 'disabled;'])],
            cause='Der Dienst apache2 läuft nicht und ist auch nicht für den Systemstart aktiviert. Auf Port 80 lauscht deshalb nichts.',
            evidence='systemctl: inactive (dead), disabled · ss: kein Eintrag für Port 80 · curl: Couldn’t connect.',
            fix=['sudo systemctl enable --now apache2'],
            verify=['systemctl status apache2  →  Active: active (running)', 'ss -tlnp  →  *:80 LISTEN', 'curl -I http://localhost  →  HTTP/1.1 200 OK'],
            explanation='„Connection refused“ bzw. „Couldn’t connect“ bei localhost heißt: Auf dem Port wartet kein Programm. '
                        'enable sorgt für den Start beim Hochfahren, --now startet den Dienst zusätzlich sofort.')
    if key == 'svc_wrong_port':
        return dict(
            symptom=f'Laut Anleitung erreicht man die Testseite des Partnerplatzes unter http://{partner}:8080. Von {host["name"]} aus klappt das nicht; ping auf {partner} funktioniert.',
            outputs=[out(f'ping -c 3 {partner}', ping(partner, own, 'ok', rng), []),
                     out(f'nc -zv -w 3 {partner} 8080', f'nc: connect to {partner} port 8080 (tcp) failed: Connection refused', ['Connection refused']),
                     out(f'nmap -p 22,80,443,8080 {partner}', nmap(partner, partner_mac, [(22, 'open'), (80, 'open'), (443, 'closed'), (8080, 'closed')], rng),
                         ['80/tcp   open', '8080/tcp closed'])],
            cause=f'Falscher Port: Der Webdienst auf {partner} läuft auf Port 80, nicht auf 8080. Der Rechner ist erreichbar und lehnt 8080 ab.',
            evidence='nc: Connection refused für 8080 · nmap: 80/tcp open, 8080/tcp closed.',
            fix=[f'Die richtige Adresse verwenden: http://{partner}/ (Port 80)', 'Anleitung korrigieren'],
            verify=[f'nc -zv -w 3 {partner} 80  →  succeeded', f'curl -I http://{partner}/  →  HTTP/1.1 200 OK'],
            explanation='„Connection refused“ ist eine schnelle, aktive Absage: Der Rechner ist da, nur der Port ist zu. '
                        'nmap zeigt auf einen Blick, welche Ports offen sind. Im LAB nur gegen den Partnerplatz im Labornetz scannen.')
    if key == 'svc_filtered':
        return dict(
            symptom=f'Eine SSH-Verbindung von {host["name"]} zum Partnerplatz {partner} hängt nach dem Befehl und bricht erst nach langer Zeit ab. Die Webseite des Partners lädt.',
            outputs=[out(f'nc -zv -w 3 {partner} 22', f'nc: connect to {partner} port 22 (tcp) timed out: Operation now in progress', ['timed out']),
                     out(f'nc -zv -w 3 {partner} 80', f'Connection to {partner} 80 port [tcp/http] succeeded!', []),
                     out(f'nmap -p 22,80 {partner}', nmap(partner, partner_mac, [(22, 'filtered'), (80, 'open')], rng), ['22/tcp   filtered'])],
            cause=f'Port 22 auf {partner} wird von einer Firewall gefiltert: Anfragen werden still verworfen. Der Rechner selbst ist erreichbar (Port 80 offen).',
            evidence='nc: timed out für Port 22, succeeded für Port 80 · nmap: 22/tcp filtered.',
            fix=[f'Auf {partner}: Regeln prüfen mit sudo nft list ruleset',
                 'SSH aus dem Labornetz erlauben, z. B. sudo nft add rule inet filter input tcp dport 22 accept'],
            verify=[f'nmap -p 22 {partner}  →  22/tcp open', f'nc -zv -w 3 {partner} 22  →  succeeded'],
            explanation='Ein Timeout ohne Antwort spricht für eine Firewall, die Pakete verwirft (filtered). Gäbe es dort einfach '
                        'keinen Dienst, käme sofort „Connection refused“ (closed). Der Vergleich mit einem offenen Port zeigt, dass '
                        'Weg und Rechner in Ordnung sind.')
    raise ValueError('Unbekannter Fehler.')


FAULTS = {
    'mask': dict(theme='adressierung', title='Falsche Netzmaske',
                 terms=['Präfix', 'Direkt verbundenes Netz', 'Default-Route', 'Network is unreachable']),
    'wrong_net': dict(theme='adressierung', title='Adresse im falschen Netz',
                      terms=['Direkt verbundenes Netz', 'Gateway', 'Network is unreachable']),
    'duplicate': dict(theme='adressierung', title='Doppelte IP-Adresse',
                      terms=['Doppelte IP-Adresse', 'ARP', 'arping -D']),
    'down': dict(theme='adressierung', title='Schnittstelle abgeschaltet',
                 terms=['UP / LOWER_UP', 'Default-Route', 'Network is unreachable']),
    'ipv6_missing': dict(theme='adressierung', title='Globale IPv6-Adresse fehlt', ipv6=True,
                         terms=['Link-Local-Adresse', 'Default-Route', 'Network is unreachable']),
    'no_default': dict(theme='routing', title='Default-Route fehlt',
                       terms=['Default-Route', 'Direkt verbundenes Netz', 'Network is unreachable']),
    'wrong_gateway': dict(theme='routing', title='Falsches Gateway',
                          terms=['Gateway', 'ARP', 'Destination Host Unreachable']),
    'wrong_interface': dict(theme='routing', title='Route über falsche Schnittstelle',
                            terms=['ip route get', 'Gateway', 'Default-Route']),
    'dns_wrong_resolver': dict(theme='dns', title='Falscher DNS-Server',
                               terms=['DNS-Server (nameserver)', 'Timeout (DNS)', 'NXDOMAIN', 'NetworkManager (nmcli)']),
    'dns_search_typo': dict(theme='dns', title='Tippfehler in der Suchdomain',
                            terms=['Suchdomain', 'FQDN', 'NetworkManager (nmcli)']),
    'dns_nxdomain': dict(theme='dns', title='Name existiert nicht (NXDOMAIN)',
                         terms=['NXDOMAIN', 'Timeout (DNS)', 'FQDN']),
    'dns_stale_hosts': dict(theme='dns', title='Veralteter Eintrag in /etc/hosts',
                            terms=['/etc/hosts', 'dig gegenüber getent', 'TTL (DNS)']),
    'path_upstream': dict(theme='pfad', title='Weg endet hinter der Firewall',
                          terms=['Hop', '* * * im traceroute', 'TTL (IP)']),
    'path_loop': dict(theme='pfad', title='Routing-Schleife',
                      terms=['Routing-Schleife', 'TTL (IP)', 'Hop']),
    'path_blackhole': dict(theme='pfad', title='PMTU-Blackhole',
                           terms=['MTU', 'DF-Bit', 'Paketgröße bei ping', 'Path MTU Discovery', 'PMTU-Blackhole', 'MSS-Clamping']),
    'path_local_mtu': dict(theme='pfad', title='Falsche MTU an der Schnittstelle',
                           terms=['MTU', 'Paketgröße bei ping', 'NetworkManager (nmcli)']),
    'svc_localhost': dict(theme='dienste', title='Dienst lauscht nur auf 127.0.0.1',
                          terms=['Port', 'LISTEN', 'Loopback (127.0.0.1)', '0.0.0.0 / [::]']),
    'svc_not_running': dict(theme='dienste', title='Dienst läuft nicht',
                            terms=['systemd-Dienst', 'Connection refused', 'LISTEN']),
    'svc_wrong_port': dict(theme='dienste', title='Falscher Port',
                           terms=['Port', 'Connection refused', 'open / closed / filtered']),
    'svc_filtered': dict(theme='dienste', title='Port von Firewall gefiltert',
                         terms=['Timeout (TCP)', 'Connection refused', 'open / closed / filtered', 'Firewall']),
}


def terms_for(variant, part_b=True):
    """Glossary of all selected parts; older saved tasks without stored terms use their fault's list."""
    keys = []
    if part_b:
        keys += variant['solution'].get('terms') or FAULTS.get(variant['fault'], {}).get('terms', [])
    keys += variant.get('live', {}).get('terms', []) + variant.get('pt', {}).get('terms', [])
    return [dict(term=key, text=GLOSSARY[key]) for key in dict.fromkeys(keys) if key in GLOSSARY]


def parts_label(cfg):
    # Display letters follow the worksheet order: A = outputs (part_b), B = live (part_a), C = Packet Tracer.
    parts = [name for name, key in (('A', 'part_b'), ('B', 'part_a'), ('C', 'part_c')) if cfg.get(key, key == 'part_b')]
    return 'Teil ' + '+'.join(parts) + (' · Wireshark' if cfg.get('wireshark') else '')


def noise_output(host, key, rng):
    """A healthy extra output for advanced levels; it never shows the fault itself."""
    s = host['school']
    src = str(ip.ip_interface(s['v4']).ip)
    if key == 'down':
        lab = str(ip.ip_interface(host['lab']['v4']).ip)
        return out(f"ping -c 3 {host['lab']['gateway']}", ping(host['lab']['gateway'], lab, 'ok', rng), noise=True)
    if key == 'ipv6_missing':
        return out(f'ping -c 3 {INTERNET4}', ping(INTERNET4, src, 'ok', rng), noise=True)
    if key in ('no_default', 'wrong_interface'):
        return out('ip neigh', neighbors(host), noise=True)
    if key.startswith('dns_') or key in ('path_loop', 'path_upstream'):
        return out('ip route', '\n'.join(routes4(host)), noise=True)
    if key == 'path_blackhole':
        return out(f"dig +short www.example.org", INTERNET4, noise=True)
    if key in ('svc_wrong_port', 'svc_filtered', 'svc_localhost'):
        return out('ip -br address', '\n'.join(
            ['lo               UNKNOWN        127.0.0.1/8 ::1/128'] +
            [f"{host[k]['name']:<16} UP             {host[k]['v4']}" for k in ('school', 'lab')]), noise=True)
    if key == 'svc_not_running':
        return out(f"ping -c 3 {host['school']['gateway']}", ping(host['school']['gateway'], src, 'ok', rng), noise=True)
    return out('ip -br link', '\n'.join(
        ['lo               UNKNOWN        00:00:00:00:00:00 <LOOPBACK,UP,LOWER_UP>'] +
        [f"{host[k]['name']:<16} {'UP':<14} {host[k]['mac']} <BROADCAST,MULTICAST,UP,LOWER_UP>" for k in ('school', 'lab')]), noise=True)


def generate(settings, seed, stamp=None):
    from dashboard.cli_exercise_parts import live_part, pt_order, pt_part
    cfg = options(settings)
    rng = random.Random(seed)
    # Parts A/C draw from their own stream: adding them never changes the part B scenarios.
    parts_rng = random.Random(str(seed) + ':parts')
    used, parts_used = [], []
    stamp = stamp or datetime.now(BERLIN).strftime('%d.%m.%Y')
    pool = eligible(cfg) if cfg['fault'] == 'zufall' else [cfg['fault']]
    order = rng.sample(pool, len(pool))
    pt_faults = pt_order(cfg, parts_rng) if cfg['part_c'] else []
    variants = []
    for number in range(1, cfg['teams'] + 1):
        key = order[(number - 1) % len(order)]
        host = healthy_host(rng, used, cfg['ipv6'])
        result = scenario(host, rng, key)
        outputs = result['outputs']
        if cfg['level'] != 'einstieg':
            outputs.insert(rng.randrange(1, len(outputs)+1), noise_output(host, key, rng))
        for row in outputs:
            row['card'] = cfg['level'] == 'profi' and row is not outputs[0]
        variants.append(dict(
            team=number, fault=key, theme=FAULTS[key]['theme'], fault_title=FAULTS[key]['title'],
            host=dict(name=host['name'], user=host['user'], school=host['school']['name'], lab=host['lab']['name']),
            symptom=result['symptom'], outputs=outputs,
            solution=dict(cause=result['cause'], evidence=result['evidence'], fix=result['fix'],
                          verify=result['verify'], explanation=result['explanation'],
                          terms=list(FAULTS[key]['terms']))))
        if cfg['part_a']:
            variants[-1]['live'] = live_part(cfg, stamp, parts_rng, parts_used)
        if cfg['part_c']:
            variants[-1]['pt'] = pt_part(cfg, parts_rng, parts_used, number, pt_faults[(number - 1) % len(pt_faults)])
    return variants


def expanded(task, variant):
    values = dict(team=str(variant['team']), rechner=variant['host']['name'], niveau=LEVELS[task['options']['level']])
    return {key: re.sub(r'\{\{(.*?)\}\}', lambda m: values[m[1]], text) for key, text in texts(task['text']).items()}


def task_label(row):
    date = datetime.fromisoformat(row['created']).astimezone(BERLIN).strftime('%d.%m.%Y %H:%M')
    cfg = row['options']
    names = ' / '.join(name for name in row.get('team_names', []) if name)
    themes = ' + '.join(THEMES[t] for t in cfg['themes'])
    return (row['title'] + (' · ' + names if names else '') + ' · ' + LEVELS[cfg['level']] + ' · ' + parts_label(cfg)
            + ' · ' + themes + ' · ' + str(cfg['teams']) + ' Team(s) · ' + date + ' · ' + row['id'][:6])


class CliExercises(Exercises):
    default_text = DEFAULT_TEXT
    default_options = DEFAULT_OPTIONS
    texts = staticmethod(texts)
    options = staticmethod(options)
    generate = staticmethod(generate)

    @staticmethod
    def label(row):
        return task_label(row)

    @staticmethod
    def normalize(cfg):
        return cfg

    @staticmethod
    def summary(row):
        part_b = row['options'].get('part_b', True)
        faults = [dict(team=v['team'], title=v['fault_title'], host=v['host']['name']) for v in row['variants'] if part_b]
        faults += [dict(team=v['team'], title='PT: ' + v['pt']['fault']['title'], host='') for v in row['variants'] if 'pt' in v]
        return dict(faults=faults, parts=parts_label(row['options']))

    def families(self, *args, **kwargs):
        raise ValueError('Diese Aktion gibt es im Linux-Generator nicht.')

    def state(self, user, page=1):
        data = super().state(user, page)
        data['catalog'] = dict(
            levels=LEVELS, themes=THEMES,
            faults=[dict(id=key, title=fault['title'], theme=fault['theme'], ipv6=bool(fault.get('ipv6')))
                    for key, fault in FAULTS.items()])
        return data
