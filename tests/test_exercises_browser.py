"""Real browser controls with the offline exercise backend, no LAB devices."""
import json
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from urllib.parse import urlparse

from tests import test_dashboard_browser as fixtures
from dashboard.exercises import Exercises
from dashboard.exercise_api import view
from dashboard.exercise_outputs import pdf, solution_zip

BASE=fixtures.BASE
expect=fixtures.expect if fixtures.sync_playwright else None


@unittest.skipUnless(fixtures.sync_playwright,'Optional local browser runtime')
class ExerciseBrowserTests(unittest.TestCase):
    def setUp(self):
        fixtures.BrowserWorkflowTests.setUp(self)
        self.store=Exercises(Path(self.enterContext(TemporaryDirectory())))
        self.user=dict(role='reader',name='Test-Lehrkraft',account='teacher',source='fixture')
        self.role='reader'

    def route(self, route):
        path=urlparse(route.request.url).path.removeprefix('/cisco-lab')
        if not path.startswith('/api/exercises'):
            return fixtures.BrowserWorkflowTests.route(self,route)
        if route.request.method=='POST':
            payload=route.request.post_data_json
            if path.endswith('/create'):data=view(self.store.create(payload['settings'],payload['text'],self.user,payload.get('team_names')))
            elif path.endswith('/preview'):data=view(self.store.preview(payload['settings'],payload['text'],self.user,payload.get('team_names')))
            elif path.endswith('/save'):data=view(self.store.save(payload['identity'],payload['text'],self.user,payload.get('team_names')))
            elif path.endswith('/text'):data=view(self.store.revise(payload['identity'],payload['text'],self.user,payload.get('team_names')))
            elif path.endswith('/families'):data=view(self.store.families(payload['identity'],payload['ipv4'],payload['ipv6'],payload['text'],self.user,payload.get('team_names')))
            elif path.endswith('/standard'):data=self.store.standard(payload['text'],payload['settings'],self.user)
            elif path.endswith('/template'):data=self.store.template(payload['identity'],payload['text'],payload['settings'],self.user)
            elif path.endswith('/delete-all'):data=self.store.delete_all(payload['token'],payload['confirmed'],self.user)
            else:self.store.delete(payload['kind'],payload['identity'],self.user);data=dict(deleted=True)
        elif '/task/' in path:data=view(self.store.task(path.rsplit('/',1)[-1],self.user))
        elif '/export/' in path:
            kind,identity,team=path.split('/')[-3:];task=self.store.task(identity,self.user);team=None if team=='all' else int(team)
            body=pdf(task,team) if kind=='pdf' else solution_zip(task,team)
            route.fulfill(content_type='application/pdf' if kind=='pdf' else 'application/zip',headers={'Content-Disposition':'attachment; filename="fixture.'+kind+'"'},body=body);return
        else:data=self.store.state(self.user,int(path.rsplit('/',1)[-1]) if '/page/' in path else 1)
        route.fulfill(content_type='application/json',body=json.dumps(data))

    def test_generate_switch_devices_save_template_reopen_and_download(self):
        self.page.goto(BASE+'/aufgaben')
        expect(self.page.locator('#exercise-title')).to_have_value('LAB-Routing Übungen')
        self.page.locator('#exercise-teams').select_option('8')
        self.page.locator('#exercise-sizes').select_option('random')
        self.page.locator('#exercise-wireshark').check()
        self.page.locator('#exercise-create').click()
        expect(self.page.locator('#exercise-plan svg')).to_be_visible()
        expect(self.page.locator('#exercise-meta')).to_contain_text('mit Wireshark')
        self.assertEqual(self.store.state(self.user)['pagination']['total'],0)
        self.page.locator('#exercise-save').click()
        expect(self.page.locator('#exercise-message')).to_contain_text('Aufgabenstand')
        self.page.locator('#exercise-team').select_option('8')
        self.assertEqual(self.page.locator('#exercise-plan [data-device] text').count(),0)
        self.assertGreater(self.page.locator('#exercise-plan [data-device="R1"] path').count(),0)
        self.assertGreater(self.page.locator('#exercise-plan [data-device="S1"] polygon').count(),0)
        self.assertGreater(self.page.locator('#exercise-plan [data-device="PC-A"] rect').count(),0)
        overlaps=self.page.locator('#exercise-plan svg').evaluate('''svg => {
          const labels=[...svg.querySelectorAll(':scope > text')].map(n=>({text:n.textContent,box:n.getBBox()}));
          const collisions=[];
          labels.forEach((a,i)=>labels.slice(i+1).forEach(b=>{
            const x=Math.min(a.box.x+a.box.width,b.box.x+b.box.width)-Math.max(a.box.x,b.box.x);
            const y=Math.min(a.box.y+a.box.height,b.box.y+b.box.height)-Math.max(a.box.y,b.box.y);
            if(x>1&&y>1)collisions.push([a.text,b.text]);
          }));return collisions;
        }''')
        self.assertEqual(overlaps,[])
        self.assertGreater(self.page.locator('#exercise-plan text > title').count(),0)
        self.page.locator('#exercise-plan [data-device="S2"]').click()
        expect(self.page.locator('#exercise-config')).to_contain_text('interface FastEthernet0/24')
        self.page.get_by_role('button',name='PC-B',exact=True).click()
        expect(self.page.locator('#exercise-config')).to_contain_text('Gezielte Routen')
        self.page.locator('#exercise-tab-text').click()
        expect(self.page.locator('#exercise-panel-text .jodit-wysiwyg')).to_have_count(5)
        self.assertGreater(self.page.locator('#exercise-panel-text .jodit-wysiwyg ol li').count(),0)
        self.page.locator('#exercise-panel-text .jodit-wysiwyg').nth(1).fill('Prüfen Sie die Ping-Strecke.')
        editor=self.page.locator('#exercise-panel-text .jodit-wysiwyg').nth(1)
        editor.press('Control+Home');editor.press('Control+b');editor.press('W');editor.press('Control+b')
        self.page.locator('#exercise-title').fill('Testvorlage – Ping')
        clearances=self.page.evaluate('''() => {
          const boxes=[...document.querySelectorAll('#exercise-panel-text .jodit-container')];
          const labels=['exercise-introduction','exercise-work','exercise-hints','exercise-reset-text','exercise-reset_ipv6'].map(id=>document.querySelector('label[for="'+id+'"]'));
          return boxes.map((box,i)=>({
            before:box.getBoundingClientRect().top-labels[i].getBoundingClientRect().bottom,
            after:(labels[i+1]||document.querySelector('#exercise-panel-text .exercise-buttons')).getBoundingClientRect().top-box.getBoundingClientRect().bottom
          }));
        }''')
        self.assertTrue(all(row['before']>=0 and row['after']>=15 for row in clearances),clearances)
        expect(self.page.locator('#exercise-template-save')).to_be_disabled()
        self.page.once('dialog',lambda dialog:dialog.accept('Testvorlage – Ping'))
        self.page.locator('#exercise-template-copy').click()
        expect(self.page.locator('#exercise-message')).to_contain_text('Vorlage gespeichert')
        saved=self.store.state(self.user)['templates'][0]['text']['work']
        self.assertTrue('<strong>' in saved or '<b>' in saved,saved)
        self.page.screenshot(path='.checks/exercise-review-2026-11-10/editor.png',full_page=True)
        self.page.locator('#exercise-title').fill('Überarbeitet')
        self.page.locator('#exercise-template-save').click()
        expect(self.page.locator('#exercise-template')).to_contain_text('Überarbeitet')
        self.assertEqual(len(self.store.state(self.user)['templates']),1)
        self.page.locator('#exercise-back-plan').click()
        self.page.locator('#exercise-history').get_by_role('button',name='Öffnen',exact=True).click()
        expect(self.page.locator('#exercise-preview-title')).to_have_text('LAB-Routing Übungen')
        with self.page.expect_download() as download:self.page.locator('#exercise-pdf').click()
        self.assertEqual(download.value.failure(),None)
        self.assertTrue(Path(download.value.path()).read_bytes().startswith(b'%PDF-'))
        with self.page.expect_download() as download:self.page.locator('#exercise-zip').click()
        self.assertEqual(download.value.failure(),None)
        self.assertTrue(Path(download.value.path()).read_bytes().startswith(b'PK'))
        self.assertEqual(self.errors,[])
        artifact=Path('.checks/exercise-layout-symbols');artifact.mkdir(parents=True,exist_ok=True)
        self.page.screenshot(path=str(artifact/'vorschau.png'),full_page=True)

    def test_admin_standard_and_export_current_formatting_without_saving_attempts(self):
        self.role='admin';self.user['role']='admin'
        self.page.goto(BASE+'/aufgaben')
        expect(self.page.locator('#exercise-title')).to_have_value('LAB-Routing Übungen')
        for _ in range(2):
            self.page.locator('#exercise-create').click()
            expect(self.page.locator('#exercise-message')).to_contain_text('Ungespeicherte Vorschau')
        self.assertEqual(self.store.state(self.user)['pagination']['total'],0)
        original=list(self.store.drafts.values())[-1]
        networks=original['variants']
        self.page.locator('#exercise-tab-text').click()
        editor=self.page.locator('#exercise-panel-text .jodit-wysiwyg').nth(1)
        editor.evaluate('''n=>{n.innerHTML='<ol><li><strong>Aktueller Fettdruck</strong> und <em>Kursiv</em></li><li>Zweite Zeile</li></ol>';n.dispatchEvent(new Event('input',{bubbles:true}));}''')
        reset=self.page.locator('#exercise-panel-text .jodit-wysiwyg').nth(3)
        reset.fill('Vorbereitung für Team {{team}}: nur eigene Geräte zurücksetzen.')
        self.page.locator('#exercise-title').fill('Formatierte Vorschau')
        self.page.locator('#exercise-back-plan').click()
        with self.page.expect_download() as download:self.page.locator('#exercise-pdf').click()
        data=Path(download.value.path()).read_bytes()
        self.assertIn(b'DejaVuSans-Bold',data);self.assertIn(b'DejaVuSans-Oblique',data)
        draft=list(self.store.drafts.values())[-1]
        self.assertIn('<strong>Aktueller Fettdruck</strong>',draft['text']['work'])
        self.assertIn('Vorbereitung für Team {{team}}',draft['text']['reset'])
        self.assertEqual(draft['variants'],networks)
        self.assertEqual(self.store.state(self.user)['pagination']['total'],0)
        self.page.locator('#exercise-save').click()
        expect(self.page.locator('#exercise-history')).to_contain_text(networks[0]['networks']['a']['ipv4'])
        self.assertEqual(self.store.state(self.user)['pagination']['total'],1)
        self.page.locator('#exercise-tab-text').click()
        self.page.on('dialog',lambda dialog:dialog.accept())
        expect(self.page.locator('#exercise-standard-save')).to_be_hidden()
        expect(self.page.locator('#exercise-template-save')).to_have_text('Standardvorlage aktualisieren')
        self.page.locator('#exercise-template-save').click()
        expect(self.page.locator('#exercise-message')).to_contain_text('Standardvorlage aktualisiert')
        self.page.locator('#exercise-template-save').click()
        expect(self.page.locator('#exercise-message')).to_contain_text('Standardvorlage aktualisiert')
        self.assertEqual(len(self.store.state(self.user)['templates']),0)
        self.assertNotIn('Schüler bitte mit Sie ansprechen',self.page.content())
        self.page.locator('.exercise-placeholders summary').click()
        expect(self.page.locator('.exercise-placeholders')).to_contain_text('Nummer der jeweiligen Teamvariante')
        self.page.reload()
        expect(self.page.locator('#exercise-title')).to_have_value('Formatierte Vorschau')
        self.page.locator('#exercise-tab-text').click()
        expect(self.page.locator('#exercise-panel-text .jodit-wysiwyg').nth(3)).to_contain_text('Vorbereitung für Team {{team}}')
        self.assertEqual(self.errors,[])
        self.assertFalse(any(path=='/api/classroom' for path,_ in self.calls))
        self.page.screenshot(path='.checks/exercise-draft/gui-desktop.png',full_page=True)

    def test_small_screen_dark_mode_and_ipv6_only(self):
        self.page.set_viewport_size(dict(width=390,height=844));self.page.emulate_media(color_scheme='dark')
        self.page.goto(BASE+'/aufgaben');expect(self.page.locator('#exercise-title')).to_have_value('LAB-Routing Übungen')
        self.page.locator('#exercise-ipv4').uncheck();self.page.locator('#exercise-create').click()
        expect(self.page.locator('#exercise-config')).to_contain_text('ipv6 unicast-routing')
        self.assertFalse('ip address ' in self.page.locator('#exercise-config').inner_text())
        self.assertTrue(self.page.evaluate('document.documentElement.scrollWidth <= innerWidth'))
        self.page.screenshot(path='.checks/exercise-draft/gui-mobile-dark.png',full_page=True)
        self.assertEqual(self.errors,[])

    def test_wireshark_tab_editors_shared_actions_and_german_number_validation(self):
        self.page.goto(BASE+'/aufgaben')
        expect(self.page.locator('#exercise-title')).to_have_value('LAB-Routing Übungen')
        self.page.locator('#exercise-sizes').select_option('random')
        self.page.locator('.exercise-advanced summary').click()
        self.page.locator('#exercise-lan-min').fill('12')
        self.page.locator('#exercise-create').click()
        self.assertEqual(self.page.locator('#exercise-lan-min').evaluate('n=>n.validationMessage'),'Bitte eine Präfixlänge zwischen 16 und 29 eingeben.')
        self.assertEqual(len(self.store.drafts),0)
        self.page.locator('#exercise-lan-min').fill('24')
        self.page.locator('#exercise-wireshark').check()
        self.page.locator('#exercise-ws-level').select_option('advanced')
        self.page.locator('#exercise-tab-wireshark').click()
        expect(self.page.locator('#exercise-panel-wireshark .jodit-wysiwyg')).to_have_count(4)
        expect(self.page.locator('#exercise-panel-text .jodit-wysiwyg')).to_have_count(0)
        self.page.locator('#exercise-ws_title').fill('Eigene Mitschnittaufgabe')
        self.page.locator('#exercise-panel-wireshark .jodit-wysiwyg').nth(0).fill('Eigener Arbeitsauftrag: {{filter}}')
        self.page.locator('#exercise-panel-wireshark .jodit-wysiwyg').nth(2).fill('Eigene Musterlösung zur Ping-Strecke.')
        self.page.once('dialog',lambda dialog:dialog.accept('Wireshark-Vorlage'))
        self.page.locator('#exercise-template-copy').click()
        expect(self.page.locator('#exercise-message')).to_contain_text('Neue Vorlage gespeichert')
        saved=self.store.state(self.user)['templates'][0]
        self.assertEqual(saved['text']['ws_title'],'Eigene Mitschnittaufgabe')
        self.assertIn('Eigener Arbeitsauftrag',saved['text']['ws_work'])
        self.page.locator('#exercise-tab-text').click()
        expect(self.page.locator('#exercise-panel-text #exercise-editor-actions')).to_be_visible()
        self.page.locator('#exercise-tab-text').press('ArrowRight')
        expect(self.page.locator('#exercise-tab-wireshark')).to_have_attribute('aria-selected','true')
        self.assertEqual(self.page.locator('#exercise-editor-actions').count(),1)
        self.assertEqual(self.page.locator('.jodit-container').count(),9)
        self.page.locator('#exercise-panel-wireshark [data-exercise-open="plan"]').click()
        self.page.locator('#exercise-create').click()
        expect(self.page.locator('#exercise-plan svg')).to_be_visible()
        task=list(self.store.drafts.values())[-1]
        self.assertIn('Eigene Musterlösung',task['text']['ws_solution'])
        with self.page.expect_download() as download:self.page.locator('#exercise-pdf').click()
        data=Path(download.value.path()).read_bytes()
        import shutil,subprocess
        if shutil.which('gs'):
            extracted=subprocess.run(['gs','-q','-dSAFER','-dBATCH','-dNOPAUSE','-sDEVICE=txtwrite','-sOutputFile=-','-'],input=data,capture_output=True,check=True).stdout.decode()
            self.assertIn('Eigene Mitschnittaufgabe',extracted)
            self.assertIn('Eigener Arbeitsauftrag: icmp || icmpv6',extracted)
            self.assertNotIn('Eigene Musterlösung',extracted)
        self.page.locator('#exercise-tab-wireshark').click()
        artifact=Path('.checks/exercise-wireshark-edit');artifact.mkdir(parents=True,exist_ok=True)
        self.page.screenshot(path=str(artifact/'wireshark-tab.png'),full_page=True)
        self.assertEqual(self.errors,[])

    def test_live_family_selection_team_names_reopening_and_template_deletion(self):
        template=self.store.template('',self.store.state(self.user)['defaults']['text'],{},self.user)
        self.page.goto(BASE+'/aufgaben')
        expect(self.page.locator('#exercise-team-name-1')).to_be_visible()
        self.page.locator('#exercise-template').select_option(template['id'])
        expect(self.page.locator('#exercise-template-remove')).to_be_visible()
        self.page.once('dialog',lambda dialog:dialog.accept())
        self.page.locator('#exercise-template-remove').click()
        expect(self.page.locator('#exercise-message')).to_contain_text('Vorlage entfernt')
        self.assertEqual(len(self.store.state(self.user)['templates']),0)
        self.page.locator('#exercise-teams').select_option('2')
        self.page.locator('#exercise-team-name-1').fill('Mia & Jonas')
        self.page.locator('#exercise-team-name-2').fill('Alex / Robin')
        self.page.locator('#exercise-ipv6').uncheck()
        self.page.locator('#exercise-create').click()
        expect(self.page.locator('#exercise-preview-name')).to_have_text('Mia & Jonas')
        before=deepcopy(list(self.store.drafts.values())[-1]['variants'])
        self.page.locator('#exercise-ipv6').check()
        expect(self.page.locator('#exercise-message')).to_contain_text('IP-Auswahl aktualisiert')
        both=deepcopy(list(self.store.drafts.values())[-1]['variants'])
        for old,new in zip(before,both):
            self.assertEqual(old['networks']['a']['ipv4'],new['networks']['a']['ipv4'])
        self.page.locator('#exercise-ipv4').uncheck()
        expect(self.page.locator('#exercise-config')).not_to_contain_text('ip address ')
        self.page.locator('#exercise-ipv4').check()
        expect(self.page.locator('#exercise-config')).to_contain_text('ip address ')
        self.assertEqual(list(self.store.drafts.values())[-1]['variants'],both)
        self.page.locator('#exercise-team').select_option('2')
        expect(self.page.locator('#exercise-preview-name')).to_have_text('Alex / Robin')
        self.page.locator('#exercise-save').click()
        expect(self.page.locator('#exercise-history')).to_contain_text('Mia & Jonas')
        saved=self.store.state(self.user)['runs'][0]
        self.assertEqual(self.store.task(saved['id'],self.user)['team_names'],['Mia & Jonas','Alex / Robin'])
        with self.page.expect_download() as download:self.page.locator('#exercise-pdf').click()
        data=Path(download.value.path()).read_bytes()
        import shutil,subprocess
        if shutil.which('gs'):
            extracted=subprocess.run(['gs','-q','-dSAFER','-dBATCH','-dNOPAUSE','-sDEVICE=txtwrite','-sOutputFile=-','-'],input=data,capture_output=True,check=True).stdout.decode()
            self.assertIn('Alex / Robin',extracted);self.assertNotIn('Mia & Jonas',extracted)
        artifact=Path('.checks/exercise-teams');artifact.mkdir(parents=True,exist_ok=True)
        (artifact/'team-2.pdf').write_bytes(data)
        self.page.screenshot(path=str(artifact/'namen-und-ip-auswahl.png'),full_page=True)
        self.page.reload()
        self.page.locator('#exercise-history').get_by_role('button',name='Öffnen',exact=True).click()
        expect(self.page.locator('#exercise-team-name-1')).to_have_value('Mia & Jonas')
        expect(self.page.locator('#exercise-team-name-2')).to_have_value('Alex / Robin')
        self.assertEqual(self.errors,[])

    def test_reset_preserves_collection_and_bulk_delete_removes_only_own_tasks(self):
        other=dict(self.user,account='another')
        self.store.create({},self.store.state(self.user)['defaults']['text'],other)
        self.page.goto(BASE+'/aufgaben');expect(self.page.locator('#exercise-title')).to_have_value('LAB-Routing Übungen')
        self.page.locator('#exercise-create').click();expect(self.page.locator('#exercise-plan svg')).to_be_visible()
        self.page.locator('#exercise-save').click();expect(self.page.locator('#exercise-message')).to_contain_text('Aufgabenstand')
        self.page.on('dialog',lambda dialog:dialog.accept())
        self.page.locator('#exercise-reset').click();expect(self.page.locator('#exercise-result')).to_be_hidden()
        self.assertEqual(self.store.state(self.user)['pagination']['total'],2)
        self.page.locator('#exercise-delete-all').click()
        expect(self.page.locator('#exercise-message')).to_contain_text('1 Aufgaben gelöscht')
        self.assertEqual(self.store.state(self.user)['pagination']['total'],1)


if __name__=='__main__':unittest.main()
