"""Real browser workflow for the Linux diagnosis generator with the offline backend, no LAB devices."""
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from urllib.parse import urlparse

from tests import test_dashboard_browser as fixtures
from dashboard.cli_exercises import CliExercises
from dashboard.cli_exercise_outputs import pdf, solution_zip, view

BASE = fixtures.BASE
expect = fixtures.expect if fixtures.sync_playwright else None
# Optional: LAB_SCREENSHOTS=<directory> stores review screenshots outside the repository.
SHOTS = os.environ.get('LAB_SCREENSHOTS')


@unittest.skipUnless(fixtures.sync_playwright, 'Optional local browser runtime')
class CliExerciseBrowserTests(unittest.TestCase):
    def setUp(self):
        fixtures.BrowserWorkflowTests.setUp(self)
        self.store = CliExercises(Path(self.enterContext(TemporaryDirectory())))
        self.user = dict(role='reader', name='Test-Lehrkraft', account='teacher', source='fixture')
        self.role = 'reader'

    def route(self, route):
        path = urlparse(route.request.url).path.removeprefix('/cisco-lab')
        if not path.startswith('/api/cli-exercises'):
            return fixtures.BrowserWorkflowTests.route(self, route)
        path = path.removeprefix('/api/cli-exercises')
        if route.request.method == 'POST':
            payload = route.request.post_data_json
            names = payload.get('team_names')
            if path == '/preview':
                data = view(self.store.preview(payload['settings'], payload['text'], self.user, names))
            elif path == '/save':
                data = view(self.store.save(payload['identity'], payload['text'], self.user, names))
            elif path == '/text':
                data = view(self.store.revise(payload['identity'], payload['text'], self.user, names))
            elif path == '/template':
                data = self.store.template(payload['identity'], payload['text'], payload['settings'], self.user)
            elif path == '/delete-all':
                data = self.store.delete_all(payload['token'], payload['confirmed'], self.user)
            else:
                self.store.delete(payload['kind'], payload['identity'], self.user)
                data = dict(deleted=True)
        elif path.startswith('/task/'):
            data = view(self.store.task(path.rsplit('/', 1)[-1], self.user))
        elif path.startswith('/export/'):
            kind, identity, team = path.split('/')[-3:]
            task = self.store.task(identity, self.user)
            body = pdf(task, None if team == 'all' else int(team)) if kind == 'pdf' else solution_zip(task, None if team == 'all' else int(team))
            route.fulfill(content_type='application/pdf' if kind == 'pdf' else 'application/zip',
                          headers={'Content-Disposition': 'attachment; filename="fixture.' + kind + '"'}, body=body)
            return
        else:
            data = self.store.state(self.user, int(path.rsplit('/', 1)[-1]) if '/page/' in path else 1)
        route.fulfill(content_type='application/json', body=json.dumps(data))

    def shot(self, name):
        if SHOTS:
            Path(SHOTS).mkdir(parents=True, exist_ok=True)
            self.page.screenshot(path=str(Path(SHOTS) / name), full_page=True)

    def test_generate_toggle_solution_save_reopen_and_download(self):
        self.page.goto(BASE + '/linux-aufgaben')
        expect(self.page.locator('#cli-title')).to_have_value('LAB-Fehlersuche im Netz')
        expect(self.page.locator('nav a[href$="/linux-aufgaben"]')).to_contain_text('Fehlersuche im Netz')
        expect(self.page.locator('#cli-fault optgroup')).to_have_count(5)
        self.page.locator('input[name=cli-theme][value=adressierung]').uncheck()
        expect(self.page.locator('#cli-fault optgroup')).to_have_count(4)
        expect(self.page.locator('#cli-fault option[value=path_blackhole]')).to_have_count(1)
        expect(self.page.locator('#cli-fault option[value=dns_stale_hosts]')).to_have_count(1)
        self.page.locator('input[name=cli-theme][value=adressierung]').check()
        self.page.locator('#cli-ipv6').check()
        expect(self.page.locator('#cli-fault option[value=ipv6_missing]')).to_have_count(1)
        self.page.locator('#cli-teams').select_option('3')
        expect(self.page.locator('#cli-team-names input')).to_have_count(3)
        self.page.locator('#cli-team-names input').first.fill('Mia & Jonas')
        self.shot('01-start.png')
        self.page.locator('#cli-create').click()
        expect(self.page.locator('#cli-meta')).to_contain_text('Ungespeicherte Vorschau')
        expect(self.page.locator('#cli-preview-name')).to_have_text('Mia & Jonas')
        expect(self.page.locator('.cli-terminal').first).to_be_visible()
        self.assertGreaterEqual(self.page.locator('.cli-terminal').count(), 3)
        expect(self.page.locator('#cli-solution')).to_be_hidden()
        self.assertEqual(self.store.state(self.user)['pagination']['total'], 0)
        self.shot('02-vorschau.png')
        self.page.locator('#cli-solution-toggle').click()
        expect(self.page.locator('#cli-solution')).to_be_visible()
        expect(self.page.locator('#cli-solution')).to_contain_text('Korrektur')
        self.assertGreater(self.page.locator('.show-solution .cli-line.is-evidence').count(), 0)
        self.shot('03-loesung.png')
        self.page.locator('#cli-team').select_option('2')
        expect(self.page.locator('#cli-preview-name')).to_be_hidden()
        self.page.locator('#cli-save').click()
        expect(self.page.locator('#cli-message')).to_contain_text('gespeichert')
        expect(self.page.locator('#cli-history .cli-chip')).to_have_count(3)
        with self.page.expect_download() as download:
            self.page.locator('#cli-pdf').click()
        self.assertTrue(Path(download.value.path()).read_bytes().startswith(b'%PDF-'))
        with self.page.expect_download() as download:
            self.page.locator('#cli-zip').click()
        self.assertTrue(Path(download.value.path()).read_bytes().startswith(b'PK'))
        self.page.locator('#cli-tab-text').click()
        expect(self.page.locator('#cli-panel-text .jodit-wysiwyg')).to_have_count(3)
        self.page.locator('#cli-title').fill('Diagnose Routing')
        self.page.once('dialog', lambda dialog: dialog.accept('Diagnose Routing'))
        self.page.locator('#cli-template-copy').click()
        expect(self.page.locator('#cli-message')).to_contain_text('Vorlage gespeichert')
        self.shot('04-texte.png')
        self.page.locator('#cli-back').click()
        self.page.locator('#cli-history').get_by_role('button', name='Öffnen', exact=True).click()
        expect(self.page.locator('#cli-meta')).to_contain_text('Gespeicherter Stand')
        self.assertEqual(self.errors, [])

    def test_dns_scenario_shows_dns_help_and_highlights_resolver_line(self):
        self.page.goto(BASE + '/linux-aufgaben')
        self.page.locator('#cli-fault').select_option('dns_wrong_resolver')
        self.page.locator('#cli-create').click()
        expect(self.page.locator('#cli-symptom')).to_contain_text('Webseiten')
        expect(self.page.locator('.cli-terminal pre').filter(has_text='cat /etc/resolv.conf')).to_have_count(1)
        self.page.locator('#cli-solution-toggle').click()
        expect(self.page.locator('.cli-line.is-evidence').filter(has_text='nameserver')).to_have_count(1)
        expect(self.page.locator('.cli-solution-head')).to_contain_text('Falscher DNS-Server')
        expect(self.page.locator('.cli-glossary dt').filter(has_text='NXDOMAIN')).to_have_count(1)
        self.shot('07-dns.png')
        self.page.locator('#cli-fault').select_option('path_blackhole')
        self.page.locator('#cli-create').click()
        expect(self.page.locator('.cli-solution-head')).to_contain_text('PMTU-Blackhole')
        expect(self.page.locator('.cli-glossary dt').filter(has_text='DF-Bit')).to_have_count(1)
        self.shot('08-mtu.png')
        self.assertEqual(self.errors, [])

    def test_all_parts_show_live_tasks_capture_fault_card_and_hide_solutions_until_toggled(self):
        self.page.goto(BASE + '/linux-aufgaben')
        expect(self.page.locator('#cli-wireshark')).to_be_disabled()
        self.page.locator('#cli-part-a').check()
        self.page.locator('#cli-wireshark').check()
        self.page.locator('#cli-part-c').check()
        self.page.locator('#cli-teams').select_option('2')
        self.page.locator('#cli-create').click()
        expect(self.page.locator('#cli-view-a .cli-table tr')).to_have_count(10)
        expect(self.page.locator('#cli-view-a .cli-step')).to_have_count(3)
        expect(self.page.locator('#cli-view-c .cli-faultcard')).to_contain_text('Fehlerkarte · nur für Team 1')
        expect(self.page.locator('#cli-view-c')).to_contain_text('Fehlerdatei geht an Team 2')
        expect(self.page.locator('#cli-view-a .cli-expected').first).to_be_hidden()
        expect(self.page.locator('#cli-view-b .cli-part-title')).to_be_visible()
        self.shot('09-teile.png')
        self.page.locator('#cli-solution-toggle').click()
        expect(self.page.locator('#cli-view-a .cli-expected').first).to_be_visible()
        expect(self.page.locator('#cli-view-c .cli-solution')).to_contain_text('So wird es gefunden')
        expect(self.page.locator('#cli-terms')).to_contain_text('TCP-Handshake')
        self.shot('10-teile-loesung.png')
        self.page.locator('#cli-solution-toggle').click()
        self.page.locator('#cli-part-b').uncheck()
        self.page.locator('#cli-part-a').uncheck()
        expect(self.page.locator('#cli-wireshark')).not_to_be_checked()
        self.page.locator('#cli-part-c').uncheck()
        expect(self.page.locator('#cli-create')).to_be_disabled()
        self.page.locator('#cli-part-c').check()
        self.page.locator('#cli-create').click()
        expect(self.page.locator('#cli-view-b')).to_be_hidden()
        expect(self.page.locator('#cli-view-c')).to_be_visible()
        self.assertEqual(self.errors, [])

    def test_profi_marks_output_cards_and_dark_theme_keeps_terminals_readable(self):
        self.page.emulate_media(color_scheme='dark')
        self.page.goto(BASE + '/linux-aufgaben')
        self.page.locator('input[name=cli-level][value=profi]').check(force=True)
        self.page.locator('#cli-fault').select_option('wrong_interface')
        self.page.locator('#cli-create').click()
        expect(self.page.locator('.cli-terminal.is-card')).to_have_count(3)
        expect(self.page.locator('#cli-export-hint')).to_contain_text('Ausgabekarten')
        self.page.locator('#cli-solution-toggle').click()
        expect(self.page.locator('.cli-solution-head')).to_contain_text('Route über falsche Schnittstelle')
        self.shot('05-profi-dunkel.png')
        self.page.set_viewport_size({'width': 390, 'height': 900})
        overflow = self.page.evaluate('document.documentElement.scrollWidth > window.innerWidth + 1')
        self.assertFalse(overflow)
        self.shot('06-handy.png')
        self.assertEqual(self.errors, [])


if __name__ == '__main__':
    unittest.main()
