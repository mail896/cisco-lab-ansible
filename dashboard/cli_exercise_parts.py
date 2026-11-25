"""Parts A (live, read-only exploration on the real LAB client), Wireshark and C (Packet Tracer team swap).

Nothing here contacts a device. Expected values for part A come from the APP inventory
at generation time and are stored with the task together with that date.
"""
import ipaddress as ip

from dashboard.exercises import PROTECTED, allocate
from dashboard.targets import LAB_TARGETS

LAB_POOL = ip.ip_network('192.168.0.0/16')
PT_POOL = ip.ip_network('172.16.0.0/12')


def school_values():
    """Client range and school network as configured in the APP inventory."""
    addresses = {host: ip.ip_address(value) for host, value in LAB_TARGETS.items()}
    network = ip.ip_network(str(next(iter(addresses.values()))) + '/16', strict=False)
    first = str(network.network_address + 1)
    return dict(network=str(network), prefix=network.prefixlen, first=first,
                seats=[dict(host=host, address=str(value)) for host, value in addresses.items()])


# (Thema, Auftrag, Befehl, Lösungsweg, Erwartung mit Platzhaltern)
LIVE_TASKS = (
    ('adressierung', 'Ermitteln Sie IPv4-Adresse und Präfix Ihres Schulanschlusses. Notieren Sie den Namen der Schnittstelle.',
     'ip -br address', 'Die Zeile mit UP und einer Adresse aus dem Schulnetz ist der Schulanschluss; das Präfix steht hinter dem Schrägstrich.',
     'Adresse Ihres Platzes laut Tabelle, Präfix /{prefix}, Netz {network}.'),
    ('adressierung', 'Welche Schnittstelle ist der USB-Laboradapter, und woran erkennen Sie ihn?',
     'ip -br link', 'Der Name beginnt mit enx, danach folgt seine MAC-Adresse. Ohne eingestellte Übung hat er keine IPv4-Adresse.',
     'Schnittstelle enx… ; Schulanschluss ohne enx-Präfix.'),
    ('routing', 'Notieren Sie die Default-Route und die Schnittstelle, über die sie führt.',
     'ip route', 'Die Zeile „default via … dev …“ nennt Gateway und Schnittstelle.',
     'default via {first} über den Schulanschluss; keine Default-Route über den Laboradapter.'),
    ('routing', 'Über welches Gateway und welche Schnittstelle würde ein Paket an 9.9.9.9 gesendet? (Es wird dabei nichts gesendet.)',
     'ip route get 9.9.9.9', 'ip route get berechnet nur den Weg aus der Routingtabelle.',
     '9.9.9.9 via {first} dev <Schulanschluss> src <eigene Adresse>.'),
    ('dns', 'Welcher DNS-Server und welche Suchdomain sind eingetragen?',
     'cat /etc/resolv.conf', 'nameserver nennt den DNS-Server, search die Suchdomain.',
     'nameserver {first} (der Schulserver); search mit der Schuldomain.'),
    ('dns', 'Lösen Sie www.wikipedia.org auf. Notieren Sie status, TTL und den antwortenden Server.',
     'dig www.wikipedia.org', 'status steht im HEADER, die TTL in der ANSWER SECTION (zweite Spalte), der Server in der Zeile SERVER.',
     'status: NOERROR; SERVER: {first}#53; die TTL ist von Abfrage zu Abfrage verschieden.'),
    ('pfad', 'Verfolgen Sie den Weg zu www.wikipedia.org. Welcher Hop ist der erste, und ab wo erscheinen nur noch * * *?',
     'traceroute -n -m 15 www.wikipedia.org', 'Hop 1 ist das eigene Gateway. Firewalls filtern traceroute-Pakete häufig, dann folgen nur noch * * *.',
     'Hop 1: {first}. Sterne danach sind an der Schul-Firewall normal und kein Fehler.'),
    ('pfad', 'Welche MTU haben Ihre Schnittstellen?',
     'ip link show', 'Der Wert steht hinter „mtu“ in der ersten Zeile jeder Schnittstelle.',
     'Schulanschluss und Laboradapter: mtu 1500; lo: mtu 65536.'),
    ('dienste', 'Welche Dienste lauschen auf Ihrem Rechner, und sind sie nur lokal oder auch von außen erreichbar?',
     'ss -tlnp', 'Local Address 0.0.0.0 bzw. [::] heißt: von außen erreichbar; 127.0.0.1 nur lokal.',
     'Mindestens Port 22 (SSH) auf 0.0.0.0 und [::]; weitere Ports, z. B. für die Bildschirmübertragung des Lehrerrechners, nennen und einordnen.'),
)
LIVE_TERMS = {'adressierung': ['Präfix'], 'routing': ['Default-Route', 'ip route get'], 'dns': ['DNS-Server (nameserver)', 'TTL (DNS)'],
              'pfad': ['Hop', '* * * im traceroute', 'MTU'], 'dienste': ['LISTEN', 'Loopback (127.0.0.1)', '0.0.0.0 / [::]']}


def live_part(cfg, stamp, rng, used):
    values = school_values()
    tasks = []
    for theme, task, command, method, expected in LIVE_TASKS:
        if theme in cfg['themes']:
            tasks.append(dict(theme=theme, task=task, command=command, method=method, expected=expected.format(**values)))
    terms = [term for theme in cfg['themes'] for term in LIVE_TERMS.get(theme, [])]
    result = dict(tasks=tasks, seats=values['seats'], stamp=stamp, network=values['network'], terms=terms)
    if cfg['wireshark']:
        net = allocate(LAB_POOL, 24, rng, used)
        a, b = str(net.network_address + 1), str(net.network_address + 2)
        result['capture'] = dict(
            network=str(net), a=a + '/24', b=b + '/24',
            setup=f'Stellen Sie über „Labornetz einstellen“ ein: Platz A {a}/24, Platz B {b}/24, keine Routen. '
                  'Verbinden Sie die beiden USB-Laboradapter direkt oder über einen Switch.',
            steps=[dict(task='Starten Sie Wireshark auf „USB-Labornetz“ mit dem Anzeigefilter arp || icmp. Pingen Sie danach den Partnerplatz.',
                        command=f'ping -c 4 {b}   (an Platz B: ping -c 4 {a})', filter='arp || icmp',
                        question='Wer fragt per ARP nach wem, und wer antwortet? Ordnen Sie Echo Request und Echo Reply über die Sequenznummer zu.',
                        expected='Zuerst ARP: „Who has <Partner>? Tell <eigene Adresse>“ als Broadcast, dann die ARP-Antwort mit der MAC des Partners. '
                                 'Danach vier Echo Requests und vier Echo Replies mit gleicher Kennung und gleicher Sequenznummer. '
                                 'Ist die MAC schon bekannt, fehlt der ARP-Austausch; das ist kein Fehler.'),
                   dict(task='Vergleichen Sie die MAC-Adresse aus der ARP-Antwort mit Ihrem Nachbarcache.',
                        command='ip neigh', filter='arp',
                        question='Steht dieselbe MAC-Adresse im Cache? In welchem Zustand?',
                        expected='ip neigh zeigt die Partneradresse mit derselben MAC wie in der ARP-Antwort, Zustand REACHABLE oder STALE.'),
                   dict(task='Prüfen Sie den SSH-Port des Partners und beobachten Sie den Verbindungsaufbau.',
                        command=f'nmap -p 22 {b}   (an Platz B: nmap -p 22 {a})', filter='tcp.port == 22',
                        question='Welche TCP-Flags sehen Sie in welcher Reihenfolge? Passt das zur Ausgabe von nmap?',
                        expected='Port offen: SYN → SYN, ACK → ACK, danach Verbindungsabbau (FIN oder RST); nmap meldet open. '
                                 'Port geschlossen: SYN → RST, ACK; nmap meldet closed. Keine Antwort: filtered.')])
        result['terms'] = terms + ['ARP', 'Echo Request / Reply', 'TCP-Handshake', 'Anzeigefilter']
    return result


# --- Teil C -------------------------------------------------------------------
PT_FAULTS = {
    'pt_mask': dict(theme='adressierung', title='Falsche Subnetzmaske an PC-A',
                    card='Ändern Sie an PC-A die Adresse auf {pca_alt} und die Subnetzmaske auf 255.255.255.240.',
                    symptom='PC-A erreicht sein Gateway und alle anderen Netze nicht.',
                    detect='ipconfig an PC-A: Maske 255.255.255.240, Gateway {r1a} liegt außerhalb von {pca_alt}/28; ping {r1a} schlägt fehl.',
                    fix='PC-A: Subnetzmaske 255.255.255.0 (Adresse darf bleiben).', terms=['Präfix', 'Gateway']),
    'pt_shutdown': dict(theme='adressierung', title='Router-Schnittstelle abgeschaltet',
                        card='R2: interface GigabitEthernet0/0/0 → shutdown. Speichern Sie mit copy running-config startup-config.',
                        symptom='Niemand aus LAN A erreicht LAN B; PC-B und Server erreichen ihr Gateway nicht.',
                        detect='R2: show ip interface brief → Gi0/0/0 administratively down; ping {r2b} von PC-B schlägt fehl.',
                        fix='R2: interface Gi0/0/0 → no shutdown.', terms=['UP / LOWER_UP']),
    'pt_gateway': dict(theme='routing', title='Falsches Gateway an PC-B',
                       card='Tragen Sie an PC-B als Default Gateway {wrong_gw} ein.',
                       symptom='PC-B erreicht Geräte in LAN B, aber nichts außerhalb; LAN A erreicht PC-B nicht (Antworten fehlen).',
                       detect='ipconfig an PC-B: Gateway {wrong_gw}; ping {wrong_gw} ohne Antwort, ping {r2b} funktioniert.',
                       fix='PC-B: Default Gateway {r2b}.', terms=['Gateway', 'Default-Route']),
    'pt_route': dict(theme='routing', title='Statische Route fehlt',
                     card='R1: Entfernen Sie die Route zu LAN B: no ip route {lanb_net} {lanb_mask} {r2t}.',
                     symptom='LAN A erreicht R1 und das Transfernetz, aber nicht LAN B.',
                     detect='R1: show ip route → keine Zeile für {lanb}; tracert von PC-A endet bei R1 mit „Destination host unreachable“.',
                     fix='R1: ip route {lanb_net} {lanb_mask} {r2t}.', terms=['Default-Route', 'Hop']),
    'pt_dns': dict(theme='dns', title='Falscher DNS-Server an PC-A',
                   card='Tragen Sie an PC-A als DNS-Server {wrong_dns} ein.',
                   symptom='PC-A erreicht den Server per IP-Adresse, aber nicht www.lab.example.',
                   detect='ipconfig /all an PC-A: DNS {wrong_dns}; nslookup www.lab.example ohne Antwort, ping {srv} funktioniert.',
                   fix='PC-A: DNS-Server {srv}.', terms=['DNS-Server (nameserver)', 'Timeout (DNS)']),
    'pt_dns_record': dict(theme='dns', title='Falscher DNS-Eintrag auf dem Server',
                          card='Server-B, Dienst DNS: Ändern Sie den Eintrag www.lab.example auf die Adresse {wrong_record}.',
                          symptom='Die Webseite www.lab.example lädt nicht; per IP-Adresse {srv} funktioniert sie.',
                          detect='nslookup www.lab.example an PC-A liefert {wrong_record} statt {srv}.',
                          fix='Server-B, DNS: www.lab.example → {srv}.', terms=['DNS-Server (nameserver)', 'FQDN']),
    'pt_wrong_hop': dict(theme='pfad', title='Route zeigt auf falschen nächsten Router',
                         card='R1: Ersetzen Sie die Route zu LAN B: no ip route {lanb_net} {lanb_mask} {r2t}, dann ip route {lanb_net} {lanb_mask} {spare}.',
                         symptom='LAN A erreicht LAN B nicht; R1 ist erreichbar.',
                         detect='tracert von PC-A: Hop 1 {r1a}, danach nur Zeitüberschreitungen; R1: show ip route → {lanb} via {spare}, dort steht kein Router.',
                         fix='R1: Route auf {r2t} korrigieren.', terms=['Hop', 'Gateway']),
    'pt_http': dict(theme='dienste', title='Webdienst ausgeschaltet',
                    card='Server-B, Services → HTTP: schalten Sie HTTP aus (Off).',
                    symptom='ping und nslookup funktionieren, die Webseite www.lab.example lädt nicht.',
                    detect='Webbrowser an PC-A: „Request Timeout“ bzw. keine Seite; ping {srv} und nslookup funktionieren.',
                    fix='Server-B: HTTP wieder einschalten (On).', terms=['Port', 'systemd-Dienst']),
}


def pt_order(cfg, rng):
    pool = [key for key, row in PT_FAULTS.items() if row['theme'] in cfg['themes']] or list(PT_FAULTS)
    return rng.sample(pool, len(pool))


def pt_part(cfg, rng, used, team, key):
    lan_a = allocate(PT_POOL, 24, rng, used)
    transfer = allocate(PT_POOL, 29, rng, used)
    lan_b = allocate(PT_POOL, 24, rng, used)
    a, t, b = lan_a.network_address, transfer.network_address, lan_b.network_address
    values = dict(lana=str(lan_a), lanb=str(lan_b), transfer=str(transfer),
                  lanb_net=str(b), lanb_mask=str(lan_b.netmask),
                  r1a=str(a + 1), r1t=str(t + 1), r2t=str(t + 2), r2b=str(b + 1),
                  pca=str(a + 10), pca_alt=str(a + 100), pcb=str(b + 20), srv=str(b + 10),
                  wrong_gw=str(b + 254), wrong_dns=str(b + 99), wrong_record=str(b + 77), spare=str(t + 3))
    devices = [
        dict(device='PC-A', port='FastEthernet0', address=values['pca'] + '/24', gateway=values['r1a'], dns=values['srv']),
        dict(device='S1', port='Fa0/1 zu PC-A, Gi0/1 zu R1', address='—', gateway='—', dns='—'),
        dict(device='R1', port='Gi0/0/0 (LAN A)', address=values['r1a'] + '/24', gateway='—', dns='—'),
        dict(device='R1', port='Gi0/0/1 (Transfer)', address=values['r1t'] + '/29', gateway='—', dns='—'),
        dict(device='R2', port='Gi0/0/1 (Transfer)', address=values['r2t'] + '/29', gateway='—', dns='—'),
        dict(device='R2', port='Gi0/0/0 (LAN B)', address=values['r2b'] + '/24', gateway='—', dns='—'),
        dict(device='S2', port='Fa0/1 Server-B, Fa0/2 PC-B, Gi0/1 R2', address='—', gateway='—', dns='—'),
        dict(device='Server-B', port='FastEthernet0', address=values['srv'] + '/24', gateway=values['r2b'], dns=values['srv']),
        dict(device='PC-B', port='FastEthernet0', address=values['pcb'] + '/24', gateway=values['r2b'], dns=values['srv']),
    ]
    routes = [f'R1: ip route {values["lanb_net"]} {values["lanb_mask"]} {values["r2t"]}',
              f'R2: ip route {lan_a.network_address} {lan_a.netmask} {values["r1t"]}']
    fault = PT_FAULTS[key]
    partner = team % cfg['teams'] + 1
    return dict(networks=dict(lan_a=values['lana'], transfer=values['transfer'], lan_b=values['lanb']),
                devices=devices, routes=routes, partner=partner if cfg['teams'] > 1 else None,
                fault=dict(id=key, theme=fault['theme'], title=fault['title'], card=fault['card'].format(**values),
                           symptom=fault['symptom'].format(**values), detect=fault['detect'].format(**values),
                           fix=fault['fix'].format(**values)),
                terms=list(fault['terms']))


PT_BUILD = (
    'Bauen Sie in Packet Tracer: PC-A – S1 – R1 – R2 – S2 – Server-B und PC-B (Router ISR4331, Switches 2960).',
    'Konfigurieren Sie Adressen, Gateways und DNS laut Tabelle sowie die beiden statischen Routen.',
    'Server-B: Dienst DNS mit dem Eintrag www.lab.example → Adresse von Server-B; Dienst HTTP eingeschaltet.',
    'Prüfen Sie: PC-A und PC-B pingen sich gegenseitig, nslookup www.lab.example und der Webbrowser funktionieren auf beiden PCs.',
    'Erst wenn alles funktioniert: Speichern Sie eine Kopie als Team-<Nr>-ok.pkt.',
    'Bauen Sie dann verdeckt den Fehler von Ihrer Fehlerkarte ein, speichern Sie als Team-<Nr>-fehler.pkt und geben Sie nur diese Datei weiter.',
)
PT_CHECK = (
    'Schildern Sie, was nicht funktioniert (Symptom), bevor Sie etwas ändern.',
    'PCs: ipconfig /all, ping zum eigenen Gateway, ping zur Gegenseite, tracert, nslookup www.lab.example, Webbrowser.',
    'Router: show ip interface brief, show ip route, show running-config.',
    'Notieren Sie Beleg, Ursache und Korrektur. Reparieren Sie den Fehler und prüfen Sie erneut.',
)
