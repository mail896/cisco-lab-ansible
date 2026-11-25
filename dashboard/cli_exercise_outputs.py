"""Worksheets, model solutions and output cards for the Linux CLI generator."""
from datetime import datetime
from html import escape
import io
import json
import re
import unicodedata
import zipfile

from dashboard.cli_exercise_parts import PT_BUILD, PT_CHECK
from dashboard.cli_exercises import LEVELS, SOURCES, THEMES, expanded, guide_for, help_for, parts_label, terms_for
from dashboard.exercise_outputs import page_frame, pdf_font, pdf_mono, pdf_styles, variant_at
from dashboard.exercise_richtext import pdf_flowables, plain_text
from maintenance.preview import BERLIN

TERMINAL_WIDTH = 495
ANSWER_WIDTH = 385
MARKER = '  ◀'
LIVE_NOTE = ('Führen Sie nur die genannten, lesenden Befehle aus. Sie verändern dabei nichts am Rechner. '
             'Notieren Sie Ihre Ergebnisse in der rechten Spalte.')


def part_b(task):
    return task['options'].get('part_b', True)


def view(task):
    """Rendering helpers for the GUI; stored models stay unchanged."""
    themes = task['options']['themes']
    task['help'] = [dict(command=c, purpose=p) for c, p in help_for(themes)]
    task['guide'] = guide_for(themes) if task['options']['level'] == 'einstieg' else []
    task['level_label'] = LEVELS[task['options']['level']]
    task['theme_labels'] = [THEMES[t] for t in themes]
    task['parts_label'] = parts_label(task['options'])
    for variant in task['variants']:
        variant['terms'] = terms_for(variant, part_b(task))
    return task


def export_name(task, kind, team=None):
    title = task['title']
    for old, new in (('Ü', 'Ue'), ('ü', 'ue'), ('Ä', 'Ae'), ('ä', 'ae'), ('Ö', 'Oe'), ('ö', 'oe'), ('ß', 'ss')):
        title = title.replace(old, new)
    slug = re.sub(r'[^A-Za-z0-9]+', '-', unicodedata.normalize('NFKD', title).encode('ascii', 'ignore').decode()).strip('-')[:65] or 'LAB-Linux'
    stamp = datetime.fromisoformat(task['created']).astimezone(BERLIN).strftime('%Y%m%d-%H%M%S')
    scope = 'Alle-Teams' if team is None else 'Team-' + str(team).zfill(2)
    level = LEVELS[task['options']['level']]
    return slug + '-' + level + '-' + stamp + '-' + task['id'][:6] + '-' + scope + '-' + ('Aufgabe.pdf' if kind == 'pdf' else 'Loesungen.zip')


def visible_outputs(task, variant, solution):
    return variant['outputs'] if solution else [row for row in variant['outputs'] if not row.get('card')]


def evidence_line(line, row):
    return any(token and token in line for token in row['evidence'])


def live_command(task, command):
    """Einstieg: full command, Fortgeschritten: tool only, Profi: none."""
    level = task['options']['level']
    return command if level == 'einstieg' else 'Werkzeug: ' + command.split()[0] if level == 'fortgeschritten' else ''


def wrap_mono(line, font, size, width):
    """Split one monospaced line so every piece fits; continuation keeps indent + two spaces."""
    from reportlab.pdfbase.pdfmetrics import stringWidth
    if stringWidth(line, font, size) <= width:
        return [line]
    indent = ' ' * (len(line) - len(line.lstrip(' ')) + 2)
    pieces, rest, first = [], line, True
    while rest:
        prefix = '' if first else indent
        limit = max(8, int(width / stringWidth('M', font, size)) - len(prefix))
        if len(rest) <= limit:
            pieces.append(prefix + rest)
            break
        cut = rest.rfind(' ', 0, limit + 1)
        if cut <= len(rest) - len(rest.lstrip(' ')):
            cut = limit
        pieces.append(prefix + rest[:cut].rstrip())
        rest = rest[cut:].lstrip(' ')
        first = False
    return pieces


def solution_notes(task, variant):
    text = expanded(task, variant)
    lines = ['Musterlösung · ' + text['title'], 'Aufgabe ' + task['id'] + ' · Team ' + str(variant['team'])
             + ' · ' + LEVELS[task['options']['level']] + ' · ' + parts_label(task['options']), '']
    names = task.get('team_names', [])
    if len(names) >= variant['team'] and names[variant['team']-1]:
        lines.insert(2, 'Name / Team: ' + names[variant['team']-1])
    if part_b(task):
        sol = variant['solution']
        lines += ['TEIL A · Fehler: ' + variant['fault_title'], 'Symptom: ' + variant['symptom'], '',
                  'Ursache: ' + sol['cause'], 'Beleg: ' + sol['evidence'], '', 'Korrektur:']
        lines += ['  ' + row for row in sol['fix']]
        lines += ['', 'Prüfung nach der Korrektur:'] + ['  ' + row for row in sol['verify']]
        lines += ['', 'Hintergrund: ' + sol['explanation']]
        if task['options']['level'] == 'profi':
            lines += ['', 'Ausgabekarten: nur auf Nachfrage mit passendem, begründetem Befehl ausgeben.']
    if 'live' in variant:
        live = variant['live']
        lines += ['', 'TEIL B · Live-Erkundung (Erwartung laut APP-Inventar, Stand ' + live['stamp'] + ')']
        for number, row in enumerate(live['tasks'], 1):
            lines += [f'  B{number}. {row["task"]}', f'      Befehl: {row["command"]}', f'      Weg: {row["method"]}',
                      f'      Erwartung: {row["expected"]}']
        lines += ['  Plätze: ' + ', '.join(seat['host'] + ' ' + seat['address'] for seat in live['seats'])]
        if 'capture' in live:
            capture = live['capture']
            lines += ['', 'WIRESHARK im Labornetz ' + capture['network'] + ': ' + capture['setup']]
            for number, step in enumerate(capture['steps'], 1):
                lines += [f'  W{number}. {step["task"]} Befehl: {step["command"]} · Filter: {step["filter"]}',
                          f'      Erwartung: {step["expected"]}']
    if 'pt' in variant:
        fault = variant['pt']['fault']
        lines += ['', 'TEIL C · Packet Tracer · Fehlerkarte: ' + fault['title'], '  Karte: ' + fault['card'],
                  '  Symptom beim Partnerteam: ' + fault['symptom'], '  So wird es gefunden: ' + fault['detect'],
                  '  Korrektur: ' + fault['fix']]
    terms = terms_for(variant, part_b(task))
    if terms:
        lines += ['', 'Begriffe kurz erklärt:'] + ['  ' + row['term'] + ': ' + row['text'] for row in terms]
    return '\n'.join(lines) + '\n'


def pdf(task, team=None, solution=False):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.pdfbase.pdfmetrics import stringWidth
    from reportlab.platypus import (KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table,
                                    TableStyle, XPreformatted)
    font = pdf_font()
    mono = pdf_mono()
    styles, team_style, small = pdf_styles(font)
    bold = font + '-Bold' if font == 'LAB-DejaVu' else 'Helvetica-Bold'
    term = ParagraphStyle('LABTerm', parent=styles['Normal'], fontName=mono, fontSize=6.8, leading=8.6,
                          textColor=colors.HexColor('#1f2d3a'))
    prompt = ParagraphStyle('LABPrompt', parent=term, fontSize=7.4, leading=9.5)
    ticket = ParagraphStyle('LABTicket', parent=styles['Normal'], fontSize=10, leading=14)
    label = ParagraphStyle('LABLabel', parent=small, fontName=bold, textColor=colors.HexColor('#8a5a00'))
    code = ParagraphStyle('LABCode', parent=small, fontName=mono, fontSize=7.2, leading=9.5)
    strong = ParagraphStyle('LABStrongSmall', parent=small, fontName=bold)
    cfg = task['options']
    level = cfg['level']
    several = sum(bool(cfg.get(key, key == 'part_b')) for key in ('part_a', 'part_b', 'part_c')) > 1

    def code_block(rows, width):
        return XPreformatted(escape('\n'.join(piece for row in rows for piece in wrap_mono(row, mono, code.fontSize, width))), code)

    def para(value, style='Normal'):
        return Paragraph(escape(str(value)).replace('\n', '<br/>'), small if style == 'small' else styles[style])

    def rich(value, style='Normal'):
        return pdf_flowables(value, small if style == 'small' else styles[style])

    def heading(value):
        return para(value, 'Heading1' if several else 'Heading2')

    def boxed(rows, fill, edge, dashed=False):
        table = Table([[row] for row in rows], colWidths=[495])
        table.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, -1), colors.HexColor(fill)),
                                   ('BOX', (0, 0), (-1, -1), .6, colors.HexColor(edge), None, (3, 3) if dashed else None),
                                   ('LEFTPADDING', (0, 0), (-1, -1), 10), ('RIGHTPADDING', (0, 0), (-1, -1), 10),
                                   ('TOPPADDING', (0, 0), (-1, 0), 8), ('BOTTOMPADDING', (0, -1), (-1, -1), 9)]))
        return table

    def grid(rows, widths, heights=None):
        table = Table(rows, colWidths=widths, rowHeights=heights, repeatRows=1)
        table.setStyle(TableStyle([('GRID', (0, 0), (-1, -1), .4, colors.HexColor('#ccd7df')),
                                   ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#eaf0f5')),
                                   ('VALIGN', (0, 0), (-1, -1), 'TOP'), ('TOPPADDING', (0, 0), (-1, -1), 5),
                                   ('BOTTOMPADDING', (0, 0), (-1, -1), 5)]))
        return table

    def terminal(variant, row, mark):
        user, host = variant['host']['user'], variant['host']['name']
        lines = []
        usable = TERMINAL_WIDTH - 9 - 6
        for line in row['text'].split('\n'):
            marked = mark and evidence_line(line, row)
            # Reserve the marker width on every evidence line so nothing leaves the frame.
            parts = wrap_mono(line, mono, term.fontSize, usable - (stringWidth(MARKER, mono, term.fontSize) if marked else 0))
            for number, part in enumerate(parts):
                safe = escape(part) or ' '
                if marked:
                    safe = '<font color="#b3261e"><b>' + safe + '</b></font>'
                    if number == len(parts) - 1:
                        safe += '<font color="#b3261e">' + MARKER + '</font>'
                lines.append(safe)
        command = wrap_mono(user + '@' + host + ':~$ ' + row['command'], mono, prompt.fontSize, usable)
        head_text = escape('\n'.join(command))
        head_text = head_text.replace(escape(user + '@' + host), '<font color="#2a684b">' + escape(user + '@' + host) + '</font>', 1)
        head = XPreformatted('<b>' + head_text + '</b>', prompt)
        body = XPreformatted('\n'.join(lines), term)
        block = Table([[head], [body]], colWidths=[TERMINAL_WIDTH])
        block.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#f3f6f8')),
                                   ('LINEBEFORE', (0, 0), (0, -1), 3, colors.HexColor('#2a684b')),
                                   ('LEFTPADDING', (0, 0), (-1, -1), 9), ('TOPPADDING', (0, 0), (-1, 0), 6),
                                   ('BOTTOMPADDING', (0, -1), (-1, -1), 7), ('TOPPADDING', (0, 1), (-1, 1), 2)]))
        return block

    def answer_table(rows, filled=None):
        data = [[para(key, 'small'), filled[i] if filled else ''] for i, key in enumerate(rows)]
        table = Table(data, colWidths=[110, ANSWER_WIDTH], rowHeights=None if filled else [38]*len(rows))
        table.setStyle(TableStyle([('GRID', (0, 0), (-1, -1), .4, colors.HexColor('#ccd7df')),
                                   ('BACKGROUND', (0, 0), (0, -1), colors.HexColor('#eaf0f5')),
                                   ('VALIGN', (0, 0), (-1, -1), 'TOP'), ('TOPPADDING', (0, 0), (-1, -1), 6),
                                   ('BOTTOMPADDING', (0, 0), (-1, -1), 6)]))
        return table

    def part_b_story(variant, text):
        story = [heading('Teil A · Störung aus Ausgaben diagnostizieren')] if several else []
        story += [Spacer(1, 4), *rich(text['introduction']), Spacer(1, 8),
                  boxed([Paragraph('STÖRUNGSMELDUNG · ' + escape(variant['host']['name']), label),
                         Paragraph(escape(variant['symptom']), ticket)], '#fff7e6', '#e8c77a'), Spacer(1, 10)]
        if solution:
            story.append(para('Fehler: ' + variant['fault_title'], 'Heading2'))
        story.append(para('Befehlsausgaben', 'Heading2'))
        for row in visible_outputs(task, variant, solution):
            extra = '  (Zusatzausgabe ohne Fehlerbezug)' if solution and row.get('noise') else ''
            extra += '  (Ausgabekarte)' if solution and row.get('card') else ''
            story += [KeepTogether([terminal(variant, row, solution), para(extra.strip(), 'small') if extra else Spacer(1, 0)]), Spacer(1, 7)]
        if level == 'profi' and not solution:
            story.append(para('Weitere Ausgaben erhalten Sie von der Lehrkraft, sobald Sie einen passenden Befehl nennen '
                              'und begründen, was Sie damit prüfen wollen.', 'small'))
        story += [para('Arbeitsauftrag', 'Heading2'), *rich(text['work'])]
        if level == 'einstieg' and not solution:
            themes = cfg['themes']
            story += [para('Leitfragen', 'Heading2')]
            story += pdf_flowables('<ol>' + ''.join('<li>' + escape(q) + '</li>' for q in guide_for(themes)) + '</ol>', styles['Normal'])
            rows = [[para('Befehl', 'small'), para('Zweck', 'small')]] + [[code_block([c], 150 - 12), para(p, 'small')] for c, p in help_for(themes)]
            story += [para('Befehlshilfe', 'Heading2'), grid(rows, [150, 345])]
        if plain_text(text['hints']):
            story += [para('Hinweise', 'Heading2'), *rich(text['hints'], 'small')]
        headings = ('Symptom', 'Beleg (Befehl, Zeile)', 'Ursache', 'Korrektur (Befehl)', 'Prüfung danach')
        if solution:
            sol = variant['solution']
            filled = [para(variant['symptom'], 'small'), para(sol['evidence'], 'small'), para(sol['cause'], 'small'),
                      code_block(sol['fix'], ANSWER_WIDTH - 12), code_block(sol['verify'], ANSWER_WIDTH - 12)]
            story += [para('Lösung', 'Heading2'), answer_table(headings, filled), Spacer(1, 8),
                      para('Hintergrund', 'Heading2'), para(sol['explanation'])]
        else:
            story.append(KeepTogether([para('Diagnoseprotokoll', 'Heading2'), answer_table(headings)]))
        return story

    def live_story(variant):
        live = variant['live']
        story = [PageBreak() if several else Spacer(1, 4), heading('Teil B · Live am LAB-Client erkunden'), para(LIVE_NOTE), Spacer(1, 6)]
        head = [para('Nr.', 'small'), para('Auftrag', 'small'), para('Erwartung und Lösungsweg' if solution else 'Ihr Ergebnis', 'small')]
        rows = [head]
        for number, row in enumerate(live['tasks'], 1):
            command = row['command'] if solution else live_command(task, row['command'])
            cell = [para(row['task'], 'small')] + ([code_block([command], 200 - 12)] if command else [])
            answer = [para(row['expected'], 'small'), para(row['method'], 'small')] if solution else ''
            rows.append([para('B' + str(number), 'small'), cell, answer])
        story.append(grid(rows, [28, 212, 255], None if solution else [None] + [46] * len(live['tasks'])))
        if solution:
            story += [Spacer(1, 6), para('Erwartete Werte laut APP-Inventar, Stand ' + live['stamp'] + '. Die Schulnetz-Werte '
                                         'stammen aus der Konfiguration, nicht aus einer Abfrage der Clients.', 'small'), Spacer(1, 4)]
            seats = live['seats']
            half = (len(seats) + 1) // 2
            rows = [[para('Platz', 'small'), para('Adresse', 'small'), para('Platz', 'small'), para('Adresse', 'small')]]
            for left, right in zip(seats[:half], seats[half:] + [None] * (2 * half - len(seats))):
                rows.append([para(left['host'], 'small'), para(left['address'], 'small'),
                             para(right['host'] if right else '', 'small'), para(right['address'] if right else '', 'small')])
            story.append(KeepTogether([para('Schuladressen der Plätze', 'Heading2'), grid(rows, [80, 167, 80, 168])]))
        if 'capture' in live:
            capture = live['capture']
            story += [Spacer(1, 8), KeepTogether([para('Wireshark-Mitschnitt im Labornetz', 'Heading2'), para(capture['setup'])])]
            for number, step in enumerate(capture['steps'], 1):
                block = [para('W' + str(number) + ' · ' + step['task'], 'Normal'),
                         code_block(['Befehl:  ' + step['command'], 'Filter:  ' + step['filter']], 495 - 12),
                         para(step['question'], 'small')]
                if solution:
                    block.append(boxed([para('Erwartung: ' + step['expected'], 'small')], '#eef7f2', '#bdd6c9'))
                else:
                    block.append(grid([[para('Ihre Beobachtung', 'small')], ['']], [495], [None, 52]))
                story += [Spacer(1, 6), KeepTogether(block)]
        return story

    def pt_story(variant):
        pt = variant['pt']
        story = [PageBreak() if several else Spacer(1, 4), heading('Teil C · Packet-Tracer-Teamtausch')]
        nets = pt['networks']
        story += [para(f'Netze: LAN A {nets["lan_a"]} · Transfernetz {nets["transfer"]} · LAN B {nets["lan_b"]}'), Spacer(1, 4),
                  para('Aufbau', 'Heading2')]
        story += pdf_flowables('<ol>' + ''.join('<li>' + escape(step) + '</li>' for step in PT_BUILD) + '</ol>', styles['Normal'])
        rows = [[para(h, 'small') for h in ('Gerät', 'Anschluss', 'Adresse', 'Gateway', 'DNS')]]
        rows += [[para(d['device'], 'small'), para(d['port'], 'small'), para(d['address'], 'small'),
                  para(d['gateway'], 'small'), para(d['dns'], 'small')] for d in pt['devices']]
        story += [Spacer(1, 4), grid(rows, [60, 150, 105, 90, 90]), Spacer(1, 4),
                  para('Statische Routen', 'Heading2'), code_block(pt['routes'], 495 - 12)]
        partner = ('Geben Sie Ihre Fehlerdatei an Team ' + str(pt['partner']) + ' weiter und diagnostizieren Sie die Datei, die Sie erhalten.'
                   if pt['partner'] else 'Tauschen Sie Ihre Fehlerdatei mit einem anderen Team oder geben Sie sie der Lehrkraft.')
        story += [para('Teamtausch', 'Heading2'), para(partner)]
        story += pdf_flowables('<ol>' + ''.join('<li>' + escape(step) + '</li>' for step in PT_CHECK) + '</ol>', styles['Normal'])
        fault = pt['fault']
        headings = ('Symptom', 'Beleg (Befehl, Ausgabe)', 'Ursache', 'Korrektur')
        if solution:
            filled = [para(fault['symptom'], 'small'), para(fault['detect'], 'small'),
                      para(fault['title'] + ' – Karte: ' + fault['card'], 'small'), para(fault['fix'], 'small')]
            story += [para('Lösung zur Fehlerkarte von Team ' + str(variant['team']), 'Heading2'), answer_table(headings, filled)]
        else:
            story.append(KeepTogether([para('Diagnoseprotokoll für die erhaltene Datei', 'Heading2'), answer_table(headings)]))
        return story

    def pt_card(variant):
        pt = variant['pt']
        return [PageBreak(), para('✂  Fehlerkarte · nur für Team ' + str(variant['team']), 'Heading2'),
                boxed([Paragraph('FEHLERKARTE · TEAM ' + str(variant['team']), label),
                       para('Erst einbauen, wenn das Netz vollständig funktioniert und als Team-' + str(variant['team']) + '-ok.pkt gespeichert ist.', 'small'),
                       Spacer(1, 4), Paragraph(escape(pt['fault']['card']), ticket),
                       para('Danach als Team-' + str(variant['team']) + '-fehler.pkt speichern. Die Karte nicht weitergeben.', 'small')],
                      '#fff7e6', '#c9a44e', dashed=True)]

    output = io.BytesIO()
    story = []
    variants = task['variants'] if team is None else [variant_at(task, team)]
    for index, variant in enumerate(variants):
        if index:
            story.append(PageBreak())
        text = expanded(task, variant)
        names = task.get('team_names', [])
        name = names[variant['team']-1] if len(names) >= variant['team'] else ''
        date = datetime.fromisoformat(task['created']).astimezone(BERLIN).strftime('%d.%m.%Y')
        story += [para(('Musterlösung · ' if solution else '') + text['title'], 'Heading1'),
                  para('Team ' + str(variant['team']) + ' · Aufgabe ' + task['id'][:8] + ' · Niveau: ' + LEVELS[level]
                       + ' · ' + parts_label(cfg) + ' · Themen: ' + ', '.join(THEMES[t] for t in cfg['themes']) + ' · Datum: ' + date, 'small')]
        if name:
            story.append(Paragraph(escape(name), team_style))
        if part_b(task):
            story += part_b_story(variant, text)
        if 'live' in variant:
            story += live_story(variant)
        if 'pt' in variant:
            story += pt_story(variant)
        terms =terms_for(variant, part_b(task))
        if solution and terms:
            glossary = Table([[Paragraph(escape(row['term']), strong), para(row['text'], 'small')] for row in terms],
                             colWidths=[130, 365])
            glossary.setStyle(TableStyle([('LINEBELOW', (0, 0), (-1, -2), .4, colors.HexColor('#dfe6ec')),
                                          ('VALIGN', (0, 0), (-1, -1), 'TOP'), ('TOPPADDING', (0, 0), (-1, -1), 5),
                                          ('BOTTOMPADDING', (0, 0), (-1, -1), 5), ('LEFTPADDING', (0, 0), (0, -1), 0)]))
            story += [Spacer(1, 6), para('Begriffe kurz erklärt', 'Heading2'), glossary]
        if 'pt' in variant and not solution:
            story += pt_card(variant)
        if solution and level == 'profi' and part_b(task):
            story += [PageBreak(), para('Ausgabekarten · Team ' + str(variant['team']), 'Heading1'),
                      para('Ausschneiden und nur ausgeben, wenn das Team den passenden Befehl nennt und begründet.', 'small'), Spacer(1, 8)]
            for row in variant['outputs']:
                if not row.get('card'):
                    continue
                frame = Table([[terminal(variant, row, False)]], colWidths=[495])
                frame.setStyle(TableStyle([('BOX', (0, 0), (-1, -1), .6, colors.HexColor('#9aa9b6'), None, (3, 3)),
                                           ('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 0)]))
                story += [KeepTogether([frame]), Spacer(1, 12)]
    footer = page_frame(task, font)
    SimpleDocTemplate(output, pagesize=A4, leftMargin=45, rightMargin=45, topMargin=90, bottomMargin=40,
                      title=task['title'], author='CISCO-LAB').build(story, onFirstPage=footer, onLaterPages=footer)
    return output.getvalue()


def solution_zip(task, team=None):
    output = io.BytesIO()
    variants = task['variants'] if team is None else [variant_at(task, team)]
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('Musterloesung.pdf', pdf(task, team, True))
        for variant in variants:
            prefix = 'Team-' + str(variant['team']).zfill(2) + '/'
            archive.writestr(prefix + 'Loesung.txt', solution_notes(task, variant))
            archive.writestr(prefix + 'Szenario.json', json.dumps(variant, ensure_ascii=False, indent=2))
            if part_b(task):
                for number, row in enumerate(variant['outputs'], 1):
                    archive.writestr(prefix + 'Ausgaben/' + str(number).zfill(2) + '-' + re.sub(r'[^A-Za-z0-9]+', '-', row['command']).strip('-')[:40] + '.txt',
                                     '$ ' + row['command'] + '\n' + row['text'] + '\n')
        archive.writestr('Quellen.txt', '\n'.join(SOURCES) + '\n')
    return output.getvalue()
