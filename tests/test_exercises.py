"""Exercise routing, worksheet separation and ownership; no LAB connections."""
from copy import deepcopy
from email.message import Message
import io
import ipaddress as ip
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
import zipfile

from dashboard import exercise_api
from dashboard.exercises import DEFAULT_OPTIONS, DEFAULT_TEXT, Exercises, generate, options, texts
from dashboard.exercise_outputs import configs, diagram_items, pdf, solution_zip, svg
from dashboard.server import Handler
from tests import test_dashboard_auth as auth_fixtures

USER = dict(account='teacher', name='Lehrkraft', source='oidc', uuid='fixture-one', role='reader')
OTHER = dict(USER, account='teacher2', uuid='fixture-two')
ADMIN = dict(OTHER, role='admin')


class GenerationTests(unittest.TestCase):
    def test_routes_reach_both_pcs_for_protocol_size_and_address_modes(self):
        for families in ((True,False),(False,True),(True,True)):
            for sizes in ('fixed','random'):
                for addressing in ('fixed','random'):
                    with self.subTest(families=families,sizes=sizes,addressing=addressing):
                        cfg=dict(ipv4=families[0],ipv6=families[1],sizes=sizes,addresses=addressing,teams=8)
                        variants=generate(cfg,'fixture')
                        self.assertEqual(variants,generate(cfg,'fixture'))
                        seen=[]
                        for variant in variants:
                            for net in variant['networks'].values():
                                for family in ('ipv4','ipv6'):
                                    if family in net:
                                        network=ip.ip_network(net[family])
                                        self.assertFalse(any(network.version==old.version and network.overlaps(old) for old in seen))
                                        seen.append(network)
                            devices=variant['devices']
                            for family,enabled in zip(('ipv4','ipv6'),families):
                                if not enabled:continue
                                for source,target in (('PC-A','PC-B'),('PC-B','PC-A')):
                                    destination=ip.ip_interface(devices[target]['interfaces'][0][family]).ip
                                    current=source
                                    path=[current]
                                    for _ in range(4):
                                        device=devices[current]
                                        if any(destination in ip.ip_interface(row[family]).network for row in device['interfaces']):
                                            path.append(target);break
                                        routes=[r for r in device['routes'] if r['family']==family and destination in ip.ip_network(r['destination'])]
                                        self.assertEqual(len(routes),1)
                                        gateway=ip.ip_address(routes[0]['gateway'])
                                        self.assertTrue(any(gateway in ip.ip_interface(row[family]).network for row in device['interfaces']))
                                        matches=[name for name,d in devices.items() if any(ip.ip_interface(row[family]).ip==gateway for row in d['interfaces'])]
                                        self.assertEqual(len(matches),1);current=matches[0];path.append(current)
                                    self.assertEqual(path, [source,'R1','R2',target] if source=='PC-A' else [source,'R2','R1',target])

    def test_host_addresses_are_valid_unique_and_pc_dialog_routes_are_accepted(self):
        from network.security.lab_request import validate_request
        from tests.test_network_broker import IDENTITY
        # Use a local fixed identity fixture; no network status or DNS is read.
        for seed in range(12):
            variant=generate(dict(addresses='random',sizes='random'),str(seed))[0]
            for network in ('a','b','transfer'):
                addresses=[ip.ip_interface(row['ipv4']) for d in variant['devices'].values() for row in d['interfaces'] if row['segment']==network]
                self.assertEqual(len({address.ip for address in addresses}),len(addresses))
                self.assertTrue(all(a.ip not in (a.network.network_address,a.network.broadcast_address) for a in addresses))
            for name in ('PC-A','PC-B'):
                device=variant['devices'][name]
                payload=dict(action='apply',addresses=[device['interfaces'][0][f] for f in ('ipv4','ipv6')],
                             routes=[dict(destination=r['destination'],gateway=r['gateway']) for r in device['routes']])
                validate_request(payload,IDENTITY)

    def test_invalid_ranges_disabled_protocols_and_oversized_lans_are_rejected(self):
        for value in (dict(ipv4=False,ipv6=False),dict(teams=9),dict(teams=True),dict(lan_prefix=30),dict(lan_min=29,lan_max=24),dict(unexpected=True)):
            with self.subTest(value=value),self.assertRaises(ValueError):options(value)
        with self.assertRaises(ValueError):generate(dict(teams=2,lan_prefix=16),'no-space')


class CollectionTests(unittest.TestCase):
    def setUp(self):
        self.temp=self.enterContext(TemporaryDirectory());self.store=Exercises(Path(self.temp))

    def test_family_selection_preserves_addresses_routes_and_restores_hidden_families(self):
        for ipv4,ipv6 in ((True,False),(False,True),(True,True)):
            task=self.store.preview(dict(ipv4=ipv4,ipv6=ipv6,teams=8,sizes='random'),DEFAULT_TEXT,USER)
            original=deepcopy(task['variants'])
            both=self.store.families(task['id'],True,True,task['text'],USER)
            for old,new in zip(original,both['variants']):
                for family,active in (('ipv4',ipv4),('ipv6',ipv6)):
                    if active:
                        for segment in old['networks']:self.assertEqual(old['networks'][segment][family],new['networks'][segment][family])
                        for name in old['devices']:
                            for before,after in zip(old['devices'][name]['interfaces'],new['devices'][name]['interfaces']):
                                self.assertEqual(before[family],after[family])
                            self.assertEqual([r for r in old['devices'][name]['routes'] if r['family']==family],
                                             [r for r in new['devices'][name]['routes'] if r['family']==family])
            only4=self.store.families(both['id'],True,False,both['text'],USER)
            self.assertNotIn('ipv6',only4['variants'][0]['networks']['a'])
            # Text/name revisions and a process restart must retain hidden addresses.
            revised=self.store.revise(only4['id'],dict(only4['text'],title='Netze behalten'),USER,['Team '+str(n) for n in range(8)])
            saved=self.store.save(revised['id'],revised['text'],USER)
            reopened=Exercises(Path(self.temp))
            only6=reopened.families(saved['id'],False,True,saved['text'],USER)
            restored=reopened.families(only6['id'],True,True,only6['text'],USER)
            self.assertEqual(both['variants'],restored['variants'])
            self.assertNotIn('address_pool',restored)
            self.assertNotEqual(saved['id'],restored['id'])
            self.assertFalse(reopened.task(saved['id'],USER)['options']['ipv6'])
            with self.assertRaises(ValueError):reopened.families(restored['id'],False,False,restored['text'],USER)

    def test_team_names_are_task_data_not_template_defaults_and_are_validated(self):
        from dashboard.exercise_outputs import solution_notes
        task=self.store.create(dict(teams=2),DEFAULT_TEXT,USER,['Mia & Jonas','Alex / Robin'])
        reopened=self.store.task(task['id'],OTHER)
        self.assertEqual(reopened['team_names'],['Mia & Jonas','Alex / Robin'])
        self.assertIn('Mia & Jonas',self.store.state(USER)['runs'][0]['label'])
        notes=solution_notes(reopened,reopened['variants'][1])
        self.assertIn('Alex / Robin',notes);self.assertNotIn('Mia & Jonas',notes)
        template=self.store.template('',task['text'],task['options'],USER)
        self.assertNotIn('team_names',template)
        for names in ([],['one'],['a','b','c'],['a','x'*161],['a','bad\nname']):
            with self.subTest(names=names),self.assertRaises(ValueError):
                self.store.preview(dict(teams=2),DEFAULT_TEXT,USER,names)

    def test_preview_save_and_text_revision_preserve_networks_and_existing_snapshots(self):
        for _ in range(4):draft=self.store.preview({},DEFAULT_TEXT,USER)
        self.assertFalse(draft['saved'])
        self.assertEqual(self.store.state(USER)['pagination']['total'],0)
        with self.assertRaises(FileNotFoundError):self.store.task(draft['id'],OTHER)
        saved=self.store.save(draft['id'],dict(DEFAULT_TEXT,work='<ol><li><b>Formatierter Testauftrag</b></li></ol>'),USER)
        self.assertTrue(saved['saved']);self.assertEqual(saved['variants'],draft['variants'])
        revised=self.store.revise(saved['id'],dict(saved['text'],title='Neuer Stand'),OTHER)
        self.assertNotEqual(saved['id'],revised['id']);self.assertFalse(revised['saved'])
        self.assertEqual(self.store.task(saved['id'],USER)['title'],DEFAULT_TEXT['title'])
        self.assertEqual(revised['variants'],saved['variants'])
        self.store.save(revised['id'],revised['text'],OTHER)
        self.assertEqual(self.store.state(USER)['pagination']['total'],2)
        self.assertEqual(self.store.state(USER)['runs'][0]['networks'][0]['networks'],draft['variants'][0]['networks'])
        # Rendering must not mutate or add configs/SVG to stored draft models.
        from dashboard.exercise_api import view
        view(self.store.task(saved['id'],USER))
        self.assertNotIn('svg',self.store.task(saved['id'],USER)['variants'][0])

    def test_admin_standard_persists_without_changing_saved_tasks(self):
        task=self.store.create({},DEFAULT_TEXT,USER)
        with self.assertRaises(PermissionError):self.store.standard(DEFAULT_TEXT,{},USER)
        body=dict(DEFAULT_TEXT,title='Neue Standardvorlage',work='<ol><li><strong>Erster Auftrag</strong></li><li>Zweiter Auftrag</li></ol>')
        self.store.standard(body,dict(ipv6=False),ADMIN)
        reopened=Exercises(Path(self.temp)).state(USER)
        self.assertEqual(reopened['defaults']['text']['title'],body['title'])
        self.assertIn('<ol>',reopened['defaults']['text']['work'])
        self.assertFalse(reopened['defaults']['options']['ipv6'])
        self.assertEqual(self.store.task(task['id'],USER)['title'],DEFAULT_TEXT['title'])

    def test_preview_cache_is_bounded_and_never_persisted(self):
        first=self.store.preview({},DEFAULT_TEXT,USER)
        for _ in range(128):self.store.preview({},DEFAULT_TEXT,USER)
        self.assertEqual(len(self.store.drafts),128)
        self.assertFalse(list(Path(self.temp).rglob('*.json')))
        with self.assertRaises(FileNotFoundError):self.store.task(first['id'],USER)

    def test_editable_reset_sections_preserve_legacy_defaults_and_expand_per_team(self):
        from dashboard.exercises import expanded
        from dashboard.exercise_outputs import solution_notes
        legacy={key:DEFAULT_TEXT[key] for key in ('title','introduction','work','hints')}
        self.assertEqual(texts(legacy)['reset'],DEFAULT_TEXT['reset'])
        reset='<p>Vorbereitung für <b>Team {{team}}</b>: {{lan_a}}</p>'
        task=self.store.create(dict(teams=2),dict(DEFAULT_TEXT,reset=reset,reset_ipv6='Eigene IPv6-Vorbereitung'),USER)
        original=self.store.task(task['id'],USER)
        template=self.store.template('',task['text'],task['options'],USER)
        self.store.template(template['id'],dict(task['text'],reset='Neue Vorbereitung'),task['options'],USER)
        for variant in task['variants']:
            text=expanded(task,variant)
            self.assertIn('Team '+str(variant['team']),text['reset'])
            self.assertIn(variant['networks']['a']['ipv4'],text['reset'])
            notes=solution_notes(task,variant)
            self.assertIn('Vorbereitung für Team '+str(variant['team']),notes)
            self.assertIn('Eigene IPv6-Vorbereitung',notes)
            self.assertNotIn('erase startup-config',notes)
        self.assertEqual(original['text'],self.store.task(task['id'],USER)['text'])
        self.assertTrue(pdf(task).startswith(b'%PDF-'))
        empty=self.store.create(dict(ipv6=False),dict(DEFAULT_TEXT,reset='',reset_ipv6=''),USER)
        self.assertNotIn('Zurücksetzen:',solution_notes(empty,empty['variants'][0]))

    def test_shared_read_own_template_edit_and_admin_override(self):
        template=self.store.template('',DEFAULT_TEXT,{},USER)
        self.assertFalse(self.store.state(OTHER)['templates'][0]['editable'])
        with self.assertRaises(PermissionError):self.store.template(template['id'],dict(DEFAULT_TEXT,title='Fremd'),{},OTHER)
        self.store.template(template['id'],dict(DEFAULT_TEXT,title='Überarbeitet'),{},USER)
        self.assertEqual(self.store.state(OTHER)['templates'][0]['title'],'Überarbeitet')
        self.store.template(template['id'],dict(DEFAULT_TEXT,title='Admin'),{},ADMIN)
        task=self.store.create({},DEFAULT_TEXT,USER)
        with self.assertRaises(PermissionError):self.store.delete('tasks',task['id'],OTHER)
        self.assertEqual(self.store.task(task['id'],OTHER)['variants'],task['variants'])
        self.store.delete('tasks',task['id'],ADMIN)

    def test_task_snapshot_survives_template_changes_and_reopening(self):
        template=self.store.template('',DEFAULT_TEXT,{},USER)
        task=self.store.create(template['options'],template['text'],USER)
        self.store.template(template['id'],dict(DEFAULT_TEXT,title='Neu'),dict(ipv6=False),USER)
        reopened=Exercises(Path(self.temp)).task(task['id'],OTHER)
        self.assertEqual(reopened['text'],texts(DEFAULT_TEXT))
        self.assertEqual(reopened['variants'],task['variants'])
        self.assertNotIn('owner',reopened)
        for path in Path(self.temp).rglob('*.json'):self.assertEqual(path.stat().st_mode&0o077,0)

    def test_bulk_delete_respects_ownership_snapshot_and_keeps_templates(self):
        own=self.store.create({},DEFAULT_TEXT,USER);other=self.store.create({},DEFAULT_TEXT,OTHER)
        self.store.template('',DEFAULT_TEXT,{},USER)
        preview=self.store.state(USER)['deletion'];self.assertEqual(preview['count'],1)
        self.store.create({},DEFAULT_TEXT,USER)
        with self.assertRaises(ValueError):self.store.delete_all(preview['token'],True,USER)
        preview=self.store.state(USER)['deletion'];self.assertEqual(preview['count'],2)
        self.assertEqual(self.store.delete_all(preview['token'],True,USER)['deleted'],2)
        self.assertEqual(self.store.task(other['id'],OTHER)['id'],other['id'])
        self.assertEqual(len(self.store.state(USER)['templates']),1)
        preview=self.store.state(ADMIN)['deletion'];self.assertEqual(preview['count'],1)
        self.store.delete_all(preview['token'],True,ADMIN)
        self.assertEqual(self.store.state(USER)['pagination']['total'],0)

    def test_rich_text_is_formatted_but_active_content_is_removed(self):
        body=dict(DEFAULT_TEXT,work='<p onclick="x()"><strong>Prüfen Sie</strong> die Route.</p><script>alert(1)</script><img src="https://invalid.test/track"><ol><li>Ping</li></ol>')
        task=self.store.create(dict(level='configure',addresses='random'),body,USER)
        text=task['text']['work'];self.assertIn('<strong>Prüfen Sie</strong>',text)
        for unwanted in ('onclick','script','alert','img','src=','invalid.test'):self.assertNotIn(unwanted,text)
        self.assertEqual(task['options']['level'],'plan');self.assertEqual(task['options']['addresses'],'fixed')
        self.assertTrue(pdf(task).startswith(b'%PDF-'))
        from reportlab.lib.styles import ParagraphStyle
        from dashboard.exercise_richtext import pdf_flowables
        content=pdf_flowables('<ol><li><b>Fett</b> <i>Kursiv</i> <u>Unterstrichen</u></li><li>Zwei</li></ol>',ParagraphStyle('fixture',fontName='LAB-DejaVu'))[0]
        self.assertIn('LAB-DejaVu-Bold',[f.fontName for f in content.frags])
        self.assertIn('LAB-DejaVu-Italic',[f.fontName for f in content.frags])

    def test_plain_entities_stay_stable_across_read_edit_and_export(self):
        from dashboard.exercise_richtext import rich_html, plain_text
        for value in ('Router & Switch', 'Wert < 10 > 2', '&lt;script&gt;Text&lt;/script&gt;',
                      '<p>LAN A &amp; LAN B</p>', 'Zeile 1\nZeile 2'):
            canonical=rich_html(value)
            self.assertEqual(rich_html(canonical),canonical)
            self.assertEqual(rich_html(rich_html(canonical)),canonical)
            self.assertNotIn('<script>',canonical)
        task=self.store.create({},dict(DEFAULT_TEXT,hints='Router & Switch'),USER)
        for _ in range(3):
            task=self.store.save(task['id'],task['text'],USER)
            task=self.store.task(task['id'],USER)
        self.assertEqual(self.store.state(USER)['pagination']['total'],1)
        self.assertEqual(plain_text(task['text']['hints']),'Router & Switch')
        self.assertTrue(pdf(task).startswith(b'%PDF-'))

    def test_history_reads_each_snapshot_once_and_only_formats_current_page(self):
        from unittest.mock import patch
        from dashboard.exercises import task_label
        for _ in range(12):self.store.create(dict(teams=8),DEFAULT_TEXT,USER)
        original=Path.read_text
        reads=[]
        def read(path,*args,**kwargs):
            if path.parent.name=='tasks':reads.append(path.name)
            return original(path,*args,**kwargs)
        with patch.object(Path,'read_text',read),patch('dashboard.exercises.task_label',wraps=task_label) as labels:
            result=self.store.state(USER,2)
        self.assertEqual(len(reads),12)
        self.assertEqual(len(set(reads)),12)
        self.assertEqual(labels.call_count,2)
        self.assertEqual(result['deletion']['count'],12)
        for row in result['runs']:
            self.assertNotIn('variants',row)
            self.assertNotIn('text',row)
            self.assertEqual(len(row['networks']),8)

    def test_history_is_paginated_and_placeholder_injection_is_plain_text(self):
        for _ in range(12):self.store.create({},DEFAULT_TEXT,USER)
        self.assertEqual(len(self.store.state(USER,1)['runs']),10)
        self.assertEqual(len(self.store.state(USER,2)['runs']),2)
        self.assertEqual(self.store.state(USER,2)['pagination']['total'],12)
        with self.assertRaises(ValueError):self.store.create({},dict(DEFAULT_TEXT,hints='{{unknown}}'),USER)
        task=self.store.create({},dict(DEFAULT_TEXT,title='<script>alert(1)</script>'),USER)
        self.assertNotIn('<script>',svg(task,task['variants'][0]))
        for value in ('../other','x'*32,[],None):
            with self.assertRaises(ValueError):self.store.task(value,USER)


class OutputTests(unittest.TestCase):
    def setUp(self):
        temp=self.enterContext(TemporaryDirectory());self.task=Exercises(Path(temp)).create(dict(level='plan',wireshark=True),DEFAULT_TEXT,USER)

    def test_planning_diagram_hides_answers_but_keeps_networks(self):
        variant=self.task['variants'][0]
        student=svg(self.task,variant,False);solution=svg(self.task,variant,True)
        for device in variant['devices'].values():
            for row in device['interfaces']:
                for family in ('ipv4','ipv6'):
                    self.assertNotIn(row[family],student);self.assertIn(row[family],solution)
        for net in variant['networks'].values():self.assertIn(net['ipv4'],student)
        self.task['options']['level']='configure'
        self.assertEqual(svg(self.task,variant,False),student)
        self.assertIn('Netzmaske:',solution)

    def test_configs_include_ports_both_routes_and_no_lab_default_route(self):
        variant=self.task['variants'][0];commands=configs(self.task,variant)
        for router in ('R1','R2'):
            self.assertIn('interface GigabitEthernet0/0/0',commands[router]);self.assertIn('interface GigabitEthernet0/0/1',commands[router])
            self.assertIn('ipv6 unicast-routing',commands[router]);self.assertIn('ipv6 nd ra suppress all',commands[router])
            for row in variant['devices'][router]['routes']:self.assertIn(row['gateway'],commands[router])
        self.assertIn('interface FastEthernet0/24',commands['S1'])
        self.assertNotIn('sudo',commands['PC-A']);self.assertNotIn('0.0.0.0/0',commands['PC-A'])
        self.assertNotIn('reload',commands['R1'])

    def test_pdf_and_zip_are_real_exports_of_the_same_snapshot(self):
        data=pdf(self.task);self.assertTrue(data.startswith(b'%PDF-'))
        with zipfile.ZipFile(io.BytesIO(solution_zip(self.task))) as archive:
            self.assertIn('Musterloesung.pdf',archive.namelist());self.assertIn('Team-01/PC-A.txt',archive.namelist())
            self.assertIn(self.task['id'],archive.read('Team-01/Hinweise.txt').decode())
            self.assertEqual(archive.read('Team-01/R1.cfg').decode(),configs(self.task,self.task['variants'][0])['R1'])
            for device in ('R1','R2','S1','S2'):
                self.assertEqual(archive.read('Team-01/'+device+'.cfg'),archive.read('Team-01/'+device+'.txt'))

    def test_editable_wireshark_texts_follow_level_and_selected_protocol(self):
        from dashboard.exercise_outputs import wireshark, solution_notes
        self.task['text'].update(ws_title='Eigene Analyse',ws_work='<p>Grundauftrag {{filter}}</p>',
                                 ws_advanced='<p>Vertiefung Team {{team}}</p>',ws_solution='<p>Eigene Lösung {{filter}}</p>',
                                 ws_solution_advanced='<p>Erweiterte Lösung {{lan_b}}</p>')
        variant=self.task['variants'][0]
        basic=' '.join(wireshark(self.task,variant));self.assertIn('icmp || icmpv6',basic);self.assertNotIn('Vertiefung',basic)
        self.task['options'].update(ws_level='advanced')
        advanced=' '.join(wireshark(self.task,variant));self.assertIn('Vertiefung Team 1',advanced)
        self.assertIn('Eigene Lösung',solution_notes(self.task,variant));self.assertNotIn('Grundauftrag',solution_notes(self.task,variant))
        self.assertTrue(pdf(self.task).startswith(b'%PDF-'))

    def test_rich_paragraphs_and_nested_lists_keep_indents_and_formatting(self):
        from dashboard.exercise_richtext import pdf_blocks, pdf_flowables
        from reportlab.lib.styles import ParagraphStyle
        html='<p>Ein Absatz mit <b>Fettdruck</b>.</p><ol><li><p>Langer Auftrag mit <i>Formatierung</i> und einem bewussten<br>Zeilenumbruch.</p><p>Weiterer Absatz im selben Auftrag.</p><ul><li>Unterpunkt</li></ul></li><li>Zweiter Auftrag</li></ol><p>Abschluss.</p>'
        blocks=pdf_blocks(html)
        self.assertEqual([b['marker'] for b in blocks],[None,'1.',None,'•','2.',None])
        self.assertEqual([b['depth'] for b in blocks],[0,1,1,2,1,0])
        self.assertIn('<i>Formatierung</i>',blocks[1]['html'])
        self.assertIn('<br/>',blocks[1]['html'])
        flows=pdf_flowables(html,ParagraphStyle('fixture',fontName='Helvetica',fontSize=9,leading=13))
        for flow in flows:
            flow.wrap(180,1000)
            self.assertGreater(flow.height,0)
        self.assertGreater(flows[1].style.leftIndent,flows[1].style.bulletIndent)
        self.assertEqual(flows[1].style.leftIndent,flows[2].style.leftIndent)
        self.task['text']['work']=html
        self.assertTrue(pdf(self.task).startswith(b'%PDF-'))

    def test_pdf_and_svg_use_device_shapes_with_only_one_visible_label(self):
        import re,shutil,subprocess
        from xml.etree import ElementTree as ET
        self.task['text'].update(work='<p>Prüfen Sie die Verbindungen.</p>',introduction='',hints='',reset='',reset_ipv6='')
        self.task['options']['wireshark']=False
        diagram=ET.fromstring(svg(self.task,self.task['variants'][0],False))
        ns={'s':'http://www.w3.org/2000/svg'}
        labels=[node.text for node in diagram.findall('s:text',ns)]
        for name in ('R1','R2','S1','S2','PC-A','PC-B'):
            self.assertEqual(labels.count(name),1)
            node=diagram.find("s:g[@data-device='"+name+"']",ns)
            self.assertIsNotNone(node);self.assertEqual(node.findall('.//s:text',ns),[])
            self.assertGreater(len(node.findall('.//s:path',ns))+len(node.findall('.//s:polygon',ns))+len(node.findall('.//s:rect',ns)),0)
        if shutil.which('gs'):
            value=subprocess.run(['gs','-q','-dSAFER','-dBATCH','-dNOPAUSE','-sDEVICE=txtwrite','-dFirstPage=1','-dLastPage=1','-sOutputFile=-','-'],input=pdf(self.task),capture_output=True,check=True).stdout.decode()
            for name in ('S1','S2','PC-A','PC-B'):self.assertEqual(len(re.findall(r'(?<!\w)'+re.escape(name)+r'(?!\w)',value)),1,value)

    def test_pdf_name_date_and_wireshark_heading_stay_with_body(self):
        import shutil,subprocess
        if not shutil.which('gs'):self.skipTest('Ghostscript für PDF-Textprüfung erforderlich')
        self.task.update(team_names=['Mia & Jonas'],created='2026-11-10T23:30:00+00:00')
        data=pdf(self.task)
        def page(number):
            return subprocess.run(['gs','-q','-dSAFER','-dBATCH','-dNOPAUSE','-sDEVICE=txtwrite',
                                   '-dFirstPage='+str(number),'-dLastPage='+str(number),'-sOutputFile=-','-'],
                                  input=data,capture_output=True,check=True).stdout.decode()
        first=page(1);self.assertIn('Mia & Jonas',first);self.assertIn('Datum: 11.11.2026',first)
        self.assertNotIn('Name / Team:',first);self.assertNotIn('____',first)
        found=False
        for number in (2,3,4):
            value=page(number)
            if 'Wireshark: die Ping-Strecke untersuchen' in value:
                found=True;self.assertIn('Öffnen Sie Wireshark',value)
        self.assertTrue(found)
        # A teacher may supply text longer than one page; grouping must still split.
        self.task['text']['ws_work']='<p>'+('Lange eigene Anleitung. '*220)+'</p>'
        self.assertTrue(pdf(self.task).startswith(b'%PDF-'))


class ExerciseRouteTests(unittest.TestCase):
    # Reuse real OIDC cookie/CSRF helpers without inheriting unrelated tests.
    setUp=auth_fixtures.SessionAndRouteTests.setUp
    login=auth_fixtures.SessionAndRouteTests.login
    handler=auth_fixtures.SessionAndRouteTests.handler

    def test_reader_can_create_and_export_but_csrf_and_auth_remain_required(self):
        sid,session=self.login('reader');store=Exercises(self.root/'exercises')
        def create(cookie=sid,csrf=session['csrf']):
            handler=self.handler('/api/exercises/create','POST',cookie,csrf,dict(settings={},text=DEFAULT_TEXT))
            handler.server.exercises=store;handler.do_POST();return handler
        self.assertEqual(create(csrf='wrong')._send.call_args.args[0],403)
        self.assertEqual(create(cookie=None)._send.call_args.args[0],401)
        result=create();self.assertEqual(result._send.call_args.args[0],201)
        task=json.loads(result._send.call_args.args[1]);self.assertEqual(len(task['variants']),1)
        toggle=self.handler('/api/exercises/families','POST',sid,session['csrf'],dict(identity=task['id'],ipv4=True,ipv6=False,text=task['text'],team_names=['Testteam']))
        toggle.server.exercises=store;toggle.do_POST();self.assertEqual(toggle._send.call_args.args[0],200)
        denied=self.handler('/api/exercises/standard','POST',sid,session['csrf'],dict(settings={},text=DEFAULT_TEXT))
        denied.server.exercises=store;denied.do_POST()
        self.assertEqual(denied._send.call_args.args[0],403)
        handler=self.handler('/api/exercises/export/pdf/'+task['id']+'/1',sid=sid)
        handler.server.exercises=store;handler.do_GET();self.assertEqual(handler._send.call_args.args[0],200)
        self.assertTrue(handler._send.call_args.args[1].startswith(b'%PDF-'))
        forbidden=self.handler('/api/refresh','POST',sid,session['csrf']);forbidden.do_POST()
        self.assertEqual(forbidden._send.call_args.args[0],403)


if __name__=='__main__':unittest.main()
