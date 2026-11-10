"""One topology and address model for interactive diagrams and teaching exports."""
from html import escape
from datetime import datetime
import io
import ipaddress as ip
import json
import zipfile
from pathlib import Path
import re
import unicodedata
import threading

from dashboard.exercises import expanded
from dashboard.exercise_richtext import pdf_flowables, plain_text
from dashboard.exercise_symbols import device_symbol, symbol_svg, draw_symbol
from maintenance.preview import BERLIN

PLAN_WIDTH, PLAN_HEIGHT = 1200, 520
FONT_LOCK = threading.Lock()
NODES = {'R1': (345, 165), 'S1': (245, 320), 'PC-A': (135, 450),
         'R2': (855, 165), 'S2': (955, 320), 'PC-B': (1065, 450)}
LINKS = (('PC-A', 'S1'), ('S1', 'R1'), ('R1', 'R2'), ('R2', 'S2'), ('S2', 'PC-B'))
SOURCES = (
    'https://www.cisco.com/c/en/us/td/docs/switches/lan/catalyst2960/software/release/15-0_1_se/configuration/guide/scg2960/swipv6.html',
    'https://www.wireshark.org/docs/dfref/i/icmp.html',
    'https://www.wireshark.org/docs/dfref/i/icmpv6.html',
)


def variant_at(task, team):
    if type(team) is not int or not 1 <= team <= len(task['variants']):
        raise ValueError('Unbekannte Teamvariante.')
    return task['variants'][team-1]


def export_name(task, kind, team=None):
    title=task['title'].replace('Ü','Ue').replace('ü','ue').replace('ä','ae').replace('ö','oe').replace('ß','ss')
    slug=re.sub(r'[^A-Za-z0-9]+','-',unicodedata.normalize('NFKD',title).encode('ascii','ignore').decode()).strip('-')[:65] or 'LAB-Routing'
    stamp=datetime.fromisoformat(task['created']).astimezone(BERLIN).strftime('%Y%m%d-%H%M%S')
    scope='Alle-Teams' if team is None else 'Team-'+str(team).zfill(2)
    cfg=task['options'];family='DualStack' if cfg['ipv4'] and cfg['ipv6'] else 'IPv4' if cfg['ipv4'] else 'IPv6'
    return slug+'-'+family+'-'+stamp+'-'+task['id'][:6]+'-'+scope+'-'+('Aufgabe.pdf' if kind=='pdf' else 'Loesungen.zip')


def router_config(device, cfg):
    lines = ['enable', 'configure terminal', 'hostname ' + device['name'], 'no ip domain-lookup']
    if cfg['ipv6']:
        lines.append('ipv6 unicast-routing')
    for row in device['interfaces']:
        lines += ['interface ' + row['port']]
        if 'ipv4' in row:
            address = ip.ip_interface(row['ipv4'])
            lines.append('ip address ' + str(address.ip) + ' ' + str(address.netmask))
        if 'ipv6' in row:
            lines.append('ipv6 address ' + row['ipv6'])
            if row.get('link_local'):
                lines.append('ipv6 address ' + row['link_local'] + ' link-local')
            # All exercise addresses are manual, so the school-facing PC must
            # never learn a competing default route through router advertisements.
            lines.append('ipv6 nd ra suppress all')
        lines += ['no shutdown', 'exit']
    for row in device['routes']:
        if row['family'] == 'ipv4':
            net = ip.ip_network(row['destination'])
            lines.append('ip route ' + str(net.network_address) + ' ' + str(net.netmask) + ' ' + row['gateway'])
        else:
            lines.append('ipv6 route ' + row['destination'] + ' ' + row['gateway'])
    return '\n'.join(lines + ['end', 'copy running-config startup-config', ''])


def switch_config(device, cfg):
    row = device['interfaces'][0]
    lines = ['enable', 'configure terminal', 'hostname ' + device['name'], 'no ip domain-lookup']
    for port, description in (('FastEthernet0/1', 'PC'), ('FastEthernet0/24', 'Router')):
        lines += ['interface ' + port, 'description ' + description, 'switchport mode access',
                  'switchport access vlan 1', 'no shutdown', 'exit']
    lines += ['interface Vlan1']
    if 'ipv4' in row:
        address = ip.ip_interface(row['ipv4'])
        lines.append('ip address ' + str(address.ip) + ' ' + str(address.netmask))
    if 'ipv6' in row:
        lines.append('ipv6 address ' + row['ipv6'])
    lines += ['no shutdown', 'exit']
    # Switches stay L2 devices. Their SVI is for local management, not PC routing.
    return '\n'.join(lines + ['end', 'copy running-config startup-config', ''])


def pc_text(device):
    addresses = [row[family] for row in device['interfaces'] for family in ('ipv4', 'ipv6') if family in row]
    routes = [row['destination'] + ' via ' + row['gateway'] for row in device['routes']]
    return ('Labornetz einstellen → Adressen (eine je Zeile):\n' + '\n'.join(addresses)
            + '\n\nGezielte Routen (eine je Zeile):\n' + '\n'.join(routes)
            + '\n\nÜbernehmen. Das Schulprofil und seine Default-Route unverändert lassen.\n')


def configs(task, variant):
    return {name: router_config(device, task['options']) if name.startswith('R') else
            switch_config(device, task['options']) if name.startswith('S') else pc_text(device)
            for name, device in variant['devices'].items()}


def network_label(network):
    return [network[family] for family in ('ipv4', 'ipv6') if family in network]


def address_guidance(task):
    if task['options']['addresses'] == 'random':
        return ('Die Geräteadressen variieren. Bei eigener Planung sind andere eindeutige, gültige Hostadressen '
                'und dazu passende Routen ebenfalls richtig; die Musterlösung zeigt eine mögliche Vergabe.')
    return ('Festes Schema: Router erste Hostadresse, Switch zweite, PC letzte nutzbare IPv4-Adresse; '
            'IPv6 nutzt ::1, ::2 und ::6. Im Transfernetz erhält R1 die erste, R2 die zweite Hostadresse.')


def diagram_items(task, variant, solution=True):
    """Label lanes never share the network headers, links or device symbols."""
    items = [('box', 10, 10, 425, 500, '#f2f7fd', '#b4cce3'),
             ('box', 765, 10, 425, 500, '#f3f8f1', '#c1d9b7'),
             ('box', 455, 10, 290, 100, '#fff8f7', '#e5c0c0')]
    def text(x,y,value,size=12,color='#243a50',tooltip=''):
        items.append(('text',x,y,value,size,color,tooltip))
    for key,x,title in (('a',30,'LAN A'),('b',785,'LAN B'),('transfer',475,'Transfernetz')):
        text(x,40,title,17,'#a8383b' if key=='transfer' else '#23577d' if key=='a' else '#416d2d')
        for index,value in enumerate(network_label(variant['networks'][key])):
            tooltip='Netzmaske: '+str(ip.ip_network(value).netmask) if ':' not in value else ''
            text(x,65+index*22,value,13,tooltip=tooltip)
    for a,b in LINKS:
        x1,y1=NODES[a];x2,y2=NODES[b]
        items.append(('line',x1,y1,x2,y2,'#be5457' if (a,b)==('R1','R2') else '#71869a'))
    text(474,193,'R1 / R2: Gi0/0/1',12,'#617489')
    text(326,245,'24 → Gi0/0/0',11,'#617489')
    text(783,245,'Gi0/0/0 ← 24',11,'#617489')
    text(55,377,'PC → Port 1',11,'#617489');text(1035,377,'Port 1 ← PC',11,'#617489')
    if solution:
        items.append(('box',455,215,290,160,'#fafbfc','#d9e1e8'))
    for name,(x,y) in NODES.items():
        color='#286789' if name in ('R1','S1','PC-A') else '#557f3d'
        items.append(('node',x,y,name,color));text(x+12 if name in ('R1','S1') else x-23,y+44,name,14)
        if not solution:continue
        positions={'R1':(30,135),'S1':(30,291),'PC-A':(205,430),
                   'R2':(905,135),'S2':(1002,291),'PC-B':(780,430)}
        label_x,label_y=positions[name]
        for index,row in enumerate(variant['devices'][name]['interfaces']):
            if index==1:label_x,label_y=(475,254) if name=='R1' else (475,329)
            port='Gi0/0/'+str(index) if name.startswith('R') else 'VLAN 1' if name.startswith('S') else 'LAN-USB'
            text(label_x,label_y-16,(name+' · ' if index else '')+port,10,'#617489')
            n=0
            for family in ('ipv4','ipv6'):
                if family in row:
                    value=row[family];tooltip='Netzmaske: '+str(ip.ip_interface(value).netmask) if family=='ipv4' else ''
                    text(label_x,label_y+n*19,value,11,tooltip=tooltip);n+=1
    return items


def svg(task, variant, solution=True):
    parts = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 520" role="img" aria-label="Netzplan der Aufgabe"><title>LAN A über R1 und R2 mit LAN B verbinden</title>']
    for item in diagram_items(task, variant, solution):
        kind, *v = item
        if kind == 'box':
            x,y,w,h,fill,stroke=v
            parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="12" fill="{fill}" stroke="{stroke}"/>')
        elif kind == 'line':
            x1,y1,x2,y2,color=v
            parts.append(f'<path d="M{x1} {y1}L{x2} {y2}" stroke="{color}" stroke-width="3"/>')
        elif kind == 'text':
            x,y,value,size,color,tooltip=v
            tip='<title>'+escape(tooltip)+'</title>' if tooltip else ''
            parts.append(f'<text x="{x}" y="{y}" font-size="{size}" fill="{color}" font-family="Arial,sans-serif">{tip}{escape(value)}</text>')
        else:
            x,y,name,color=v
            shape=symbol_svg(device_symbol(name,color))
            parts.append(f'<g data-device="{name}" tabindex="0" role="button" aria-label="{name}: Einstellungen öffnen"><title>{name}: Einstellungen öffnen</title><g class="device-symbol" transform="translate({x} {y})">{shape}</g></g>')
    return ''.join(parts) + '</svg>'


def wireshark(task, variant, solution=False):
    text = expanded(task, variant)
    keys = ['ws_solution' if solution else 'ws_work']
    if task['options']['ws_level'] == 'advanced':
        keys.append('ws_solution_advanced' if solution else 'ws_advanced')
    return [text[key] for key in keys if plain_text(text[key])]


def solution_notes(task, variant):
    text = expanded(task, variant)
    lines = ['Musterlösung · ' + text['title'], 'Aufgabe ' + task['id'] + ' · Team ' + str(variant['team']), '',
             'Verkabelung: PC-A—S1—R1—R2—S2—PC-B. Switch Fa0/1 zum PC, Fa0/24 zum Router.',
             'Die Switche arbeiten auf Layer 2. Ihre VLAN-1-Adresse dient nur lokalem Management.',
             'Beide PCs brauchen gezielte Routen ins gegenüberliegende LAN; beide Router den jeweiligen Rückweg.',
             'Die Default-Route zum Schulnetz bleibt unverändert.']
    names=task.get('team_names', [])
    if len(names)>=variant['team'] and names[variant['team']-1]:lines.insert(2, 'Name / Team: '+names[variant['team']-1])
    if plain_text(text['reset']):lines += ['', 'Zurücksetzen:', plain_text(text['reset'])]
    if task['options']['ipv6']:
        lines += ['', plain_text(text['reset_ipv6'])]
    lines += ['', 'Prüffolge: Link/Port → lokale Adresse und Maske → lokaler Router → Transfernetz → entfernter PC → Rückweg.',
              'Typische Fehler: falsches Präfix, Interface shutdown, fehlende PC-Route, fehlender Rückweg, falscher Switchport/VLAN.']
    if task['options']['wireshark']:
        lines += ['', text['ws_title']] + [plain_text(value) for value in wireshark(task, variant, True)]
    return '\n'.join(lines) + '\n'


def pdf(task, team=None, solution=False):
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, Flowable, KeepTogether
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    # DejaVu is already supplied by the host; embed it for German worksheet text.
    font_path = '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'
    with FONT_LOCK:
        if 'LAB-DejaVu' not in pdfmetrics.getRegisteredFontNames() and Path(font_path).is_file():
            for suffix, filename in (('', 'DejaVuSans.ttf'), ('-Bold', 'DejaVuSans-Bold.ttf'),
                                     ('-Italic', 'DejaVuSans-Oblique.ttf'), ('-BoldItalic', 'DejaVuSans-BoldOblique.ttf')):
                pdfmetrics.registerFont(TTFont('LAB-DejaVu'+suffix, str(Path(font_path).parent/filename)))
            pdfmetrics.registerFontFamily('LAB-DejaVu', normal='LAB-DejaVu', bold='LAB-DejaVu-Bold',
                                         italic='LAB-DejaVu-Italic', boldItalic='LAB-DejaVu-BoldItalic')
    font = 'LAB-DejaVu' if 'LAB-DejaVu' in pdfmetrics.getRegisteredFontNames() else 'Helvetica'
    styles = getSampleStyleSheet()
    for key in ('Normal', 'Heading1', 'Heading2'):
        styles[key].fontName = font+'-Bold' if key.startswith('Heading') and font=='LAB-DejaVu' else font
    styles['Normal'].fontSize=9; styles['Normal'].leading=13
    styles['Heading1'].fontSize=19; styles['Heading2'].fontSize=12
    styles['Heading2'].keepWithNext=True
    team_style=ParagraphStyle('LABTeam',parent=styles['Normal'],fontName=font+'-Bold' if font=='LAB-DejaVu' else 'Helvetica-Bold',fontSize=14,leading=18,textColor=colors.HexColor('#246246'),spaceBefore=6,spaceAfter=6)
    small = ParagraphStyle('LABSmall', parent=styles['Normal'], fontSize=7, leading=10, alignment=TA_LEFT)
    def para(text, style='Normal'):
        return Paragraph(escape(str(text)).replace('\n', '<br/>'), small if style == 'small' else styles[style])
    def rich(text, style='Normal'):
        return pdf_flowables(text,small if style=='small' else styles[style])
    class Plan(Flowable):
        def __init__(self, variant):
            super().__init__(); self.variant=variant
            self.width=495; self.height=PLAN_HEIGHT*495/PLAN_WIDTH
        def draw(self):
            canvas=self.canv; canvas.saveState(); canvas.scale(495/PLAN_WIDTH,495/PLAN_WIDTH)
            for item in diagram_items(task, self.variant, solution):
                kind,*v=item
                if kind=='box':
                    x,y,w,h,fill,stroke=v;canvas.setFillColor(colors.HexColor(fill));canvas.setStrokeColor(colors.HexColor(stroke));canvas.roundRect(x,PLAN_HEIGHT-y-h,w,h,12,fill=1)
                elif kind=='line':
                    x1,y1,x2,y2,color=v;canvas.setStrokeColor(colors.HexColor(color));canvas.setLineWidth(2);canvas.line(x1,PLAN_HEIGHT-y1,x2,PLAN_HEIGHT-y2)
                elif kind=='text':
                    x,y,value,size,color,*_extra=v;canvas.setFillColor(colors.HexColor(color));canvas.setFont(font,size);canvas.drawString(x,PLAN_HEIGHT-y,value)
                else:
                    x,y,name,color=v;draw_symbol(canvas,x,PLAN_HEIGHT-y,device_symbol(name,color))
            canvas.restoreState()
    output=io.BytesIO(); story=[]
    variants=task['variants'] if team is None else [variant_at(task,team)]
    for index,variant in enumerate(variants):
        if index: story.append(PageBreak())
        text=expanded(task,variant);show=solution
        names=task.get('team_names', [])
        name=names[variant['team']-1] if len(names)>=variant['team'] else ''
        date=datetime.fromisoformat(task['created']).astimezone(BERLIN).strftime('%d.%m.%Y')
        story += [para(('Musterlösung · ' if solution else '')+text['title'],'Heading1'),
                  para('Team '+str(variant['team'])+' · Aufgabe '+task['id'][:8]+' · '+(' / '.join(f.upper() for f in ('ipv4','ipv6') if task['options'][f]))+' · Datum: '+date,'small')]
        if name:story.append(Paragraph(escape(name),team_style))
        story += [Spacer(1,8),*rich(text['introduction']),Spacer(1,7),Plan(variant),
                  para('Arbeitsauftrag','Heading2'),*rich(text['work']),
                  para('Hinweise','Heading2'),*rich(text['hints']),Spacer(1,5),para(address_guidance(task),'small')]
        if plain_text(text['reset']):story += [para('Zurücksetzen vor der Übung','Heading2'),*rich(text['reset'],'small')]
        if task['options']['ipv6'] and plain_text(text['reset_ipv6']):story += [Spacer(1,5),*rich(text['reset_ipv6'],'small')]
        story += [PageBreak(),para('Adress- und Routentabelle','Heading1')]
        data=[[para(v,'small') for v in ('Gerät / Anschluss','IPv4 / Präfix','IPv6 / Präfix')]]
        for name,device in variant['devices'].items():
            for row in device['interfaces']:
                data.append([para(name+'\n'+row['port'],'small')]+[para(row.get(family,'—') if show else ('' if task['options'][family] else '—'),'small') for family in ('ipv4','ipv6')])
        table=Table(data,colWidths=[155,155,185],repeatRows=1)
        table.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#eaf0f5')),('GRID',(0,0),(-1,-1),.4,colors.HexColor('#ccd7df')),('VALIGN',(0,0),(-1,-1),'TOP'),('TOPPADDING',(0,0),(-1,-1),7),('BOTTOMPADDING',(0,0),(-1,-1),7)]));story += [table,Spacer(1,12)]
        data=[[para(v,'small') for v in ('Gerät','Zielnetz','Nächster Router')]]
        for name in ('PC-A','R1','R2','PC-B'):
            for row in variant['devices'][name]['routes']:
                data.append([para(name+' · '+row['family'].upper(),'small'),para(row['destination'] if show else '','small'),para(row['gateway'] if show else '','small')])
        table=Table(data,colWidths=[100,205,190],repeatRows=1);table.setStyle(TableStyle([('GRID',(0,0),(-1,-1),.4,colors.HexColor('#ccd7df')),('VALIGN',(0,0),(-1,-1),'TOP'),('TOPPADDING',(0,0),(-1,-1),6),('BOTTOMPADDING',(0,0),(-1,-1),6)]));story += [table,Spacer(1,10),para('Diagnoseprotokoll','Heading2')]
        def answer_table(headings, widths):
            result=Table([[para(value,'small') for value in headings]]+[['']*len(headings) for _ in range(2)],
                         colWidths=widths,rowHeights=[24,32,32],repeatRows=1)
            result.setStyle(TableStyle([('GRID',(0,0),(-1,-1),.4,colors.HexColor('#ccd7df')),
                                        ('BACKGROUND',(0,0),(-1,0),colors.HexColor('#eaf0f5')),
                                        ('VALIGN',(0,0),(-1,-1),'TOP')]))
            return result
        story.append(answer_table(('Prüfung','Beobachtung','Ursache','Korrektur'),[100,145,125,125]))
        if task['options']['wireshark']:
            block = [para(text['ws_title'],'Heading2')] if text['ws_title'] else []
            for line in wireshark(task,variant,solution):block.extend(rich(line,'small'))
            block += [Spacer(1,8),answer_table(('Paket / Richtung','Quell- / Ziel-IP','Kennung / Sequenz','Beobachtung'),[100,145,125,125])]
            story.append(KeepTogether(block))
        if solution:story += [para('Diagnose und Erwartung','Heading2'),para(solution_notes(task,variant),'small')]
    def footer(canvas,doc):
        canvas.saveState()
        logo=Path(__file__).parent/'assets/bbs-einbeck.png'
        if logo.is_file():canvas.drawImage(str(logo),A4[0]-145,A4[1]-59,width=100,height=28,preserveAspectRatio=True,mask='auto')
        canvas.setFillColor(colors.HexColor('#243a50'));canvas.setFont(font,12);canvas.drawString(45,A4[1]-39,'CISCO-Lab')
        canvas.setFont(font,9);canvas.drawString(45,A4[1]-55,'Marc Schulz')
        canvas.setStrokeColor(colors.HexColor('#cbd7e1'));canvas.line(45,A4[1]-73,A4[0]-45,A4[1]-73)
        canvas.setFont(font,7);canvas.setFillColor(colors.HexColor('#66788a'));canvas.drawString(45,24,'CISCO-LAB · '+task['id'][:8]);canvas.drawRightString(A4[0]-45,24,'Seite '+str(doc.page))
        canvas.restoreState()
    SimpleDocTemplate(output,pagesize=A4,leftMargin=45,rightMargin=45,topMargin=90,bottomMargin=40,title=task['title'],author='CISCO-LAB').build(story,onFirstPage=footer,onLaterPages=footer)
    return output.getvalue()


def solution_zip(task, team=None):
    output=io.BytesIO()
    variants=task['variants'] if team is None else [variant_at(task,team)]
    with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('Musterloesung.pdf',pdf(task,team,True))
        for variant in variants:
            prefix='Team-'+str(variant['team']).zfill(2)+'/'
            archive.writestr(prefix+'Hinweise.txt',solution_notes(task,variant))
            archive.writestr(prefix+'Netzplan.svg',svg(task,variant))
            archive.writestr(prefix+'Adressplan.json',json.dumps(variant,ensure_ascii=False,indent=2))
            for name,content in configs(task,variant).items():
                archive.writestr(prefix+name+'.txt',content)
                if name[0] in 'RS':archive.writestr(prefix+name+'.cfg',content)
        archive.writestr('Quellen.txt','\n'.join(SOURCES)+'\n')
    return output.getvalue()
