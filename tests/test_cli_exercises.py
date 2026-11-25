"""Linux CLI diagnosis generator: consistent simulated outputs, collection and routes; no LAB connections."""
import io
import ipaddress as ip
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import zipfile

from dashboard.cli_exercises import (DEFAULT_TEXT, FAULTS, INTERNET4, CliExercises, eligible, generate, options,
                                     texts)
from dashboard.exercises import PROTECTED
from tests import test_dashboard_auth as auth_fixtures

USER = dict(account='teacher', name='Lehrkraft', source='oidc', uuid='fixture-one', role='reader')
OTHER = dict(USER, account='teacher2', uuid='fixture-two')
ADMIN = dict(OTHER, role='admin')
ALL = dict(themes=['adressierung', 'routing', 'dns', 'pfad', 'dienste'], ipv6=True)


def output(variant, command):
    rows = [row for row in variant['outputs'] if row['command'] == command]
    return rows[0]['text'] if rows else None


class GenerationTests(unittest.TestCase):
    def test_every_fault_is_deterministic_and_evidence_appears_in_its_output(self):
        for fault in FAULTS:
            for level in ('einstieg', 'fortgeschritten', 'profi'):
                with self.subTest(fault=fault, level=level):
                    cfg = dict(ALL, fault=fault, level=level, teams=3)
                    variants = generate(cfg, 'seed-' + fault)
                    self.assertEqual(variants, generate(cfg, 'seed-' + fault))
                    for variant in variants:
                        self.assertEqual(variant['fault'], fault)
                        evidence = [row for row in variant['outputs'] if row['evidence']]
                        self.assertTrue(evidence)
                        for row in variant['outputs']:
                            for token in row['evidence']:
                                self.assertIn(token, row['text'])
                            if row['noise']:
                                self.assertEqual(row['evidence'], [])
                        self.assertEqual(sum(row['noise'] for row in variant['outputs']), 0 if level == 'einstieg' else 1)
                        cards = [row['card'] for row in variant['outputs']]
                        self.assertEqual(cards, [False] + [level == 'profi'] * (len(cards) - 1))
                        self.assertTrue(variant['solution']['fix'] and variant['solution']['verify'])

    def test_simulated_networks_avoid_real_lab_ranges_and_internet_uses_documentation_space(self):
        for seed in range(20):
            for variant in generate(dict(ALL, teams=8), str(seed)):
                text = '\n'.join(row['text'] for row in variant['outputs'])
                for value in __import__('re').findall(r'\b(?:\d{1,3}\.){3}\d{1,3}(?:/\d+)?\b', text):
                    address = ip.ip_interface(value)
                    self.assertFalse(any(address.ip in net for net in PROTECTED if net.version == 4), value)
                    self.assertTrue(address.ip.is_private or address.ip in ip.ip_network('198.51.100.0/24'), value)
                for net in PROTECTED:
                    # The real school /16 prefix (taken from the inventory) never appears in simulated outputs.
                    self.assertNotIn(str(net.network_address).rsplit('.', 2)[0] + '.', text)

    def test_fault_specific_state_matches_kernel_behaviour(self):
        def one(fault):
            return generate(dict(ALL, fault=fault), 'state')[0]
        for fault in ('mask', 'wrong_net', 'no_default', 'down'):
            routes = output(one(fault), 'ip route')
            self.assertNotIn('default via', routes, fault)
        down = one('down')
        self.assertIn('state DOWN', output(down, 'ip address'))
        self.assertNotIn('enp2s0', output(down, 'ip route'))
        gateway = one('wrong_gateway')
        self.assertIn('Destination Host Unreachable', output(gateway, 'ping -c 3 ' + INTERNET4))
        self.assertIn('FAILED', output(gateway, 'ip neigh'))
        wrong = one('wrong_interface')
        get = [row['text'] for row in wrong['outputs'] if row['command'].startswith('ip route get')][0]
        self.assertIn('dev enp2s0', get)
        self.assertIn('dev enx', wrong['solution']['fix'][1])
        duplicate = one('duplicate')
        arping = [row['text'] for row in duplicate['outputs'] if 'arping' in row['command']][0]
        self.assertIn('Received 1 response(s)', arping)
        flaky = [row['text'] for row in duplicate['outputs'] if row['command'].startswith('ping -c 6')][0]
        self.assertIn('50% packet loss', flaky)
        missing = one('ipv6_missing')
        self.assertNotIn('scope global', output(missing, 'ip -6 address show dev enp2s0'))
        self.assertNotIn('default', output(missing, 'ip -6 route'))
        mask = one('mask')
        prefix = [line for line in output(mask, 'ip address').splitlines() if 'enp2s0' in line and 'inet ' in line][0]
        self.assertIn('/28', prefix)

    def test_dns_faults_separate_timeout_nxdomain_search_and_hosts_file(self):
        from dashboard.cli_exercises import guide_for, help_for
        def one(fault, seed='dns'):
            return generate(dict(themes=['dns'], fault=fault), seed)[0]
        def by(variant, prefix):
            return [row['text'] for row in variant['outputs'] if row['command'].startswith(prefix)][0]
        for seed in map(str, range(10)):
            resolver = one('dns_wrong_resolver', seed)
            conf = by(resolver, 'cat /etc/resolv.conf')
            wrong = conf.split('nameserver ')[1]
            self.assertIn(wrong + '#53: timed out', by(resolver, 'dig'))
            self.assertIn('0% packet loss', by(resolver, 'ping -c 3 198.51.100.10'))
            self.assertIn('Temporary failure in name resolution', by(resolver, 'ping -c 3 www.'))
            self.assertNotIn(wrong, resolver['solution']['fix'][0])
            missing = one('dns_nxdomain', seed)
            self.assertIn('status: NXDOMAIN', by(missing, 'dig'))
            self.assertIn('AUTHORITY SECTION', by(missing, 'dig'))
            self.assertNotIn('ANSWER SECTION', by(missing, 'dig'))
            search = one('dns_search_typo', seed)
            self.assertIn('search schlue.example', by(search, 'cat /etc/resolv.conf'))
            self.assertIn('ttl=64', by(search, 'ping -c 3 drucker.'))
            stale = one('dns_stale_hosts', seed)
            new = by(stale, 'dig').split('IN      A       ')[1].split()[0]
            old = by(stale, 'getent').split()[0]
            self.assertNotEqual(old, new)
            self.assertIn('(' + old + ')', by(stale, 'ping'))
            self.assertIn(old, by(stale, 'grep intranet /etc/hosts'))
            self.assertIn(new, stale['solution']['verify'][0])
        self.assertIn('dig <Name>', [c for c, _ in help_for(['dns'])])
        self.assertNotIn('dig <Name>', [c for c, _ in help_for(['routing'])])
        self.assertTrue(all('DNS' in q or 'Name' in q or 'NXDOMAIN' in q or 'hosts' in q for q in guide_for(['dns'])))

    def test_path_faults_show_loop_mtu_boundary_and_upstream_break(self):
        def one(fault, seed):
            return generate(dict(themes=['pfad'], fault=fault), seed)[0]
        def by(variant, prefix):
            return [row['text'] for row in variant['outputs'] if row['command'].startswith(prefix)][0]
        for seed in map(str, range(10)):
            loop = one('path_loop', seed)
            hops = [line.split()[1] for line in by(loop, 'traceroute').splitlines()[1:]]
            self.assertEqual(len(hops), 8)
            self.assertEqual(len(set(hops[1:])), 2)
            self.assertTrue(all(a != b for a, b in zip(hops[1:], hops[2:])))
            self.assertIn('Time to live exceeded', by(loop, 'ping'))
            upstream = one('path_upstream', seed)
            lines = by(upstream, 'traceroute').splitlines()[1:]
            self.assertNotIn('*', lines[0] + lines[1])
            self.assertTrue(all(line.endswith('* * *') for line in lines[2:]))
            self.assertIn(' 0% packet loss', by(upstream, 'ping -c 3 172.'))
            hole = one('path_blackhole', seed)
            pings = [row for row in hole['outputs'] if '-M do -s' in row['command']]
            big, small = (int(row['command'].split('-s ')[1].split()[0]) for row in pings)
            mtu = small + 28
            self.assertEqual(big + 28, 1500)
            self.assertIn(mtu, (1492, 1480, 1460))
            self.assertIn('100% packet loss', pings[0]['text'])
            self.assertIn(f'{small}({mtu}) bytes of data', pings[1]['text'])
            self.assertIn(f'{small + 8} bytes from', pings[1]['text'])
            self.assertIn('mtu 1500', by(hole, 'ip link'))
            local = one('path_local_mtu', seed)
            self.assertIn('mtu 9000', by(local, 'ip link'))
            self.assertIn('100% packet loss', by(local, 'ping -c 3 -s 2000'))
            self.assertIn('mtu 1500', local['solution']['fix'][0])

    def test_every_solution_explains_its_terms_in_web_pdf_text_and_old_snapshots(self):
        from dashboard.cli_exercises import GLOSSARY, terms_for
        from dashboard.cli_exercise_outputs import solution_notes, view
        for fault, row in FAULTS.items():
            self.assertTrue(row['terms'], fault)
            self.assertTrue(all(term in GLOSSARY for term in row['terms']), fault)
        variant = generate(dict(ALL, fault='dns_nxdomain'), 'terms')[0]
        self.assertEqual([t['term'] for t in terms_for(variant)], FAULTS['dns_nxdomain']['terms'])
        legacy = dict(variant, solution={k: v for k, v in variant['solution'].items() if k != 'terms'})
        self.assertEqual(terms_for(legacy), terms_for(variant))
        task = dict(id='a' * 32, created='2026-11-24T20:00:00+00:00', title='T', text=DEFAULT_TEXT,
                    options=options(dict(ALL, fault='dns_nxdomain')), variants=[variant], team_names=[''])
        notes = solution_notes(task, variant)
        self.assertIn('Begriffe kurz erklärt', notes)
        self.assertIn('NXDOMAIN: ', notes)
        self.assertIn('Non-Existent Domain', view(task)['variants'][0]['terms'][0]['text'])

    def test_parts_a_and_c_use_inventory_values_lab_capture_net_and_distinct_fault_cards(self):
        from dashboard.cli_exercise_parts import PT_FAULTS, school_values
        from dashboard.targets import LAB_TARGETS
        cfg = dict(ALL, teams=8, part_a=True, part_c=True, wireshark=True)
        with_parts = generate(cfg, 'parts', stamp='24.11.2026')
        plain = generate(dict(ALL, teams=8), 'parts')
        self.assertEqual([v['outputs'] for v in with_parts], [v['outputs'] for v in plain])
        values = school_values()
        self.assertEqual([seat['address'] for seat in values['seats']], list(LAB_TARGETS.values()))
        captures, cards = [], []
        for variant in with_parts:
            live = variant['live']
            self.assertEqual(live['stamp'], '24.11.2026')
            self.assertEqual({row['theme'] for row in live['tasks']}, set(ALL['themes']))
            self.assertTrue(any(values['first'] in row['expected'] for row in live['tasks']))
            net = ip.ip_network(live['capture']['network'])
            self.assertFalse(any(net.overlaps(p) for p in PROTECTED))
            self.assertEqual(ip.ip_interface(live['capture']['a']).network, net)
            captures.append(net)
            pt = variant['pt']
            self.assertIn(pt['fault']['id'], PT_FAULTS)
            self.assertEqual(pt['partner'], variant['team'] % 8 + 1)
            cards.append(pt['fault']['id'])
            self.assertNotIn('{', pt['fault']['card'] + pt['fault']['detect'] + pt['fault']['fix'])
        self.assertEqual(len(set(captures)), 8)
        self.assertEqual(len(set(cards)), len(PT_FAULTS))
        only_dns = generate(dict(themes=['dns'], part_b=False, part_c=True, teams=3), 'dns')
        self.assertTrue(all(PT_FAULTS[v['pt']['fault']['id']]['theme'] == 'dns' for v in only_dns))
        self.assertIsNone(generate(dict(part_c=True), 'one')[0]['pt']['partner'])
        self.assertNotIn('live', plain[0])

    def test_teams_get_distinct_faults_until_the_pool_repeats_and_options_are_strict(self):
        variants = generate(dict(ALL, teams=8), 'teams')
        pool = eligible(options(ALL))
        self.assertEqual(len({v['fault'] for v in variants}), min(8, len(pool)))
        self.assertNotIn('ipv6_missing', eligible(options(dict(ALL, ipv6=False))))
        self.assertEqual({v['theme'] for v in generate(dict(themes=['routing'], teams=3), 'r')}, {'routing'})
        for value in (dict(themes=[]), dict(themes=['unbekannt']), dict(themes=['routing', 'routing']), dict(level='experte'),
                      dict(teams=9), dict(teams=True), dict(ipv6='ja'), dict(fault='unbekannt'),
                      dict(themes=['routing'], fault='mask'), dict(fault='ipv6_missing', ipv6=False), dict(extra=1),
                      dict(part_a=False, part_b=False, part_c=False), dict(wireshark=True), dict(part_c='ja')):
            with self.subTest(value=value), self.assertRaises(ValueError):
                options(value)

    def test_texts_allow_only_known_placeholders_and_sanitize_markup(self):
        body = texts(dict(DEFAULT_TEXT, work='<ol><li>Team {{team}} auf {{rechner}}</li></ol><script>x</script>'))
        self.assertNotIn('<script', body['work'])
        with self.assertRaises(ValueError):
            texts(dict(DEFAULT_TEXT, hints='{{lan_a}}'))
        with self.assertRaises(ValueError):
            texts(dict(DEFAULT_TEXT, work=''))


class CollectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = self.enterContext(TemporaryDirectory())
        self.store = CliExercises(Path(self.temp))

    def test_preview_save_revise_keep_outputs_and_summaries_list_faults(self):
        draft = self.store.preview(dict(teams=2, level='fortgeschritten'), DEFAULT_TEXT, USER, ['Mia & Jonas', ''])
        self.assertFalse(draft['saved'])
        self.assertEqual(self.store.state(USER)['pagination']['total'], 0)
        with self.assertRaises(FileNotFoundError):
            self.store.task(draft['id'], OTHER)
        saved = self.store.save(draft['id'], dict(DEFAULT_TEXT, title='Diagnose 1'), USER)
        self.assertEqual(saved['variants'], draft['variants'])
        revised = self.store.revise(saved['id'], dict(saved['text'], title='Neuer Stand'), OTHER)
        self.assertNotEqual(revised['id'], saved['id'])
        self.assertEqual(self.store.task(saved['id'], USER)['title'], 'Diagnose 1')
        state = self.store.state(USER)
        row = state['runs'][0]
        self.assertIn('Fortgeschritten', row['label'])
        self.assertIn('Mia & Jonas', row['label'])
        self.assertEqual([f['team'] for f in row['faults']], [1, 2])
        self.assertNotIn('variants', row)
        self.assertEqual({f['id'] for f in state['catalog']['faults']}, set(FAULTS))
        with self.assertRaises(ValueError):
            self.store.families(saved['id'], True, False, saved['text'], USER)

    def test_templates_standard_and_bulk_delete_follow_owner_and_admin_rules(self):
        template = self.store.template('', DEFAULT_TEXT, dict(level='profi', themes=['routing']), USER)
        self.assertEqual(template['options']['level'], 'profi')
        with self.assertRaises(PermissionError):
            self.store.template(template['id'], DEFAULT_TEXT, {}, OTHER)
        with self.assertRaises(PermissionError):
            self.store.standard(DEFAULT_TEXT, {}, USER)
        self.store.standard(dict(DEFAULT_TEXT, title='Standard Linux'), dict(level='profi'), ADMIN)
        reopened = CliExercises(Path(self.temp)).state(USER)
        self.assertEqual(reopened['defaults']['text']['title'], 'Standard Linux')
        self.assertEqual(reopened['defaults']['options']['level'], 'profi')
        for user in (USER, USER, OTHER):
            self.store.create({}, DEFAULT_TEXT, user)
        own = self.store.state(USER)['deletion']
        self.assertEqual((own['count'], own['scope']), (2, 'own'))
        self.assertEqual(self.store.delete_all(own['token'], True, USER), dict(deleted=2))
        self.assertEqual(self.store.state(ADMIN)['deletion']['count'], 1)

    def test_routing_generator_store_is_separate(self):
        from dashboard.exercises import Exercises, DEFAULT_TEXT as ROUTING_TEXT
        routing = Exercises(Path(self.temp) / 'routing')
        routing.create({}, ROUTING_TEXT, USER)
        self.store.create({}, DEFAULT_TEXT, USER)
        self.assertEqual(routing.state(USER)['pagination']['total'], 1)
        self.assertEqual(self.store.state(USER)['pagination']['total'], 1)
        self.assertIn('networks', routing.state(USER)['runs'][0])


class OutputTests(unittest.TestCase):
    def setUp(self):
        self.store = CliExercises(Path(self.enterContext(TemporaryDirectory())))

    def test_pdf_and_zip_for_all_levels_contain_solution_only_where_expected(self):
        from dashboard.cli_exercise_outputs import export_name, pdf, solution_notes, solution_zip, visible_outputs
        for level in ('einstieg', 'fortgeschritten', 'profi'):
            with self.subTest(level=level):
                task = self.store.create(dict(level=level, teams=2, ipv6=True), DEFAULT_TEXT, USER, ['Mia & Jonas', 'Alex'])
                worksheet = pdf(task, 1)
                self.assertTrue(worksheet.startswith(b'%PDF-'))
                self.assertTrue(pdf(task, None, True).startswith(b'%PDF-'))
                visible = visible_outputs(task, task['variants'][0], False)
                self.assertEqual(len(visible), 1 if level == 'profi' else len(task['variants'][0]['outputs']))
                archive = zipfile.ZipFile(io.BytesIO(solution_zip(task)))
                names = archive.namelist()
                self.assertIn('Musterloesung.pdf', names)
                self.assertIn('Team-02/Loesung.txt', names)
                self.assertIn('Quellen.txt', names)
                notes = archive.read('Team-01/Loesung.txt').decode()
                self.assertIn('Mia & Jonas', notes)
                self.assertIn(task['variants'][0]['solution']['fix'][0], notes)
                self.assertNotIn('Mia & Jonas', solution_notes(task, task['variants'][1]))
                scenario = json.loads(archive.read('Team-01/Szenario.json'))
                self.assertEqual(scenario['fault'], task['variants'][0]['fault'])
                name = export_name(task, 'pdf', 1)
                self.assertRegex(name, r'^LAB-Fehlersuche-im-Netz-(Einstieg|Fortgeschritten|Profi)-\d{8}-\d{6}-[a-f0-9]{6}-Team-01-Aufgabe\.pdf$')


    def test_all_part_combinations_export_and_solution_text_covers_selected_parts(self):
        import itertools
        from dashboard.cli_exercise_outputs import pdf, solution_notes, solution_zip, view
        for a, b, c, w in itertools.product((False, True), repeat=4):
            if not (a or b or c) or (w and not a):
                continue
            for level in ('einstieg', 'profi'):
                with self.subTest(a=a, b=b, c=c, w=w, level=level):
                    task = self.store.create(dict(level=level, teams=2, part_a=a, part_b=b, part_c=c, wireshark=w), DEFAULT_TEXT, USER)
                    self.assertTrue(pdf(task).startswith(b'%PDF-'))
                    self.assertTrue(pdf(task, 2, True).startswith(b'%PDF-'))
                    archive = zipfile.ZipFile(io.BytesIO(solution_zip(task, 1)))
                    self.assertEqual(any(name.startswith('Team-01/Ausgaben/') for name in archive.namelist()), b)
                    notes = solution_notes(task, task['variants'][0])
                    self.assertEqual('TEIL B' in notes, a)
                    self.assertEqual('TEIL A' in notes, b)
                    self.assertEqual('TEIL C' in notes, c)
                    self.assertEqual('WIRESHARK' in notes, w)
                    self.assertIn('Begriffe kurz erklärt', notes)
                    rendered = view(self.store.task(task['id'], USER))
                    self.assertTrue(rendered['variants'][0]['terms'])
                    self.assertIn('Teil', rendered['parts_label'])

    def test_every_pdf_code_line_fits_its_frame_for_all_faults_levels_and_seeds(self):
        from reportlab.pdfbase.pdfmetrics import stringWidth
        from dashboard.cli_exercise_outputs import ANSWER_WIDTH, MARKER, TERMINAL_WIDTH, wrap_mono
        from dashboard.exercise_outputs import pdf_mono
        mono = pdf_mono()
        terminal, code = TERMINAL_WIDTH - 15, ANSWER_WIDTH - 12
        for fault in FAULTS:
            for seed in range(25):
                for variant in generate(dict(ALL, fault=fault, level='profi', teams=2), fault + str(seed)):
                    for row in variant['outputs']:
                        for line in (row['command'], *row['text'].split('\n')):
                            for piece in wrap_mono(line, mono, 6.8, terminal - stringWidth(MARKER, mono, 6.8)):
                                self.assertLessEqual(stringWidth(piece + MARKER, mono, 6.8), terminal, piece)
                    for line in variant['solution']['fix'] + variant['solution']['verify']:
                        pieces = wrap_mono(line, mono, 7.2, code)
                        self.assertEqual(' '.join(p.strip() for p in pieces).split(), line.split())
                        for piece in pieces:
                            self.assertLessEqual(stringWidth(piece, mono, 7.2), code, piece)
        unbroken = 'x' * 400
        pieces = wrap_mono('    ' + unbroken, mono, 7.2, 120)
        self.assertEqual(''.join(p.strip() for p in pieces), unbroken)
        self.assertTrue(all(stringWidth(p, mono, 7.2) <= 120 for p in pieces))


class CliRouteTests(unittest.TestCase):
    setUp = auth_fixtures.SessionAndRouteTests.setUp
    login = auth_fixtures.SessionAndRouteTests.login
    handler = auth_fixtures.SessionAndRouteTests.handler

    def test_reader_can_create_and_export_with_auth_and_csrf_and_cannot_set_standard(self):
        sid, session = self.login('reader')
        store = CliExercises(self.root / 'cli-exercises')

        def post(path, payload, cookie=sid, csrf=session['csrf']):
            handler = self.handler('/api/cli-exercises' + path, 'POST', cookie, csrf, payload)
            handler.server.cli_exercises = store
            handler.do_POST()
            return handler
        self.assertEqual(post('/create', dict(settings={}, text=DEFAULT_TEXT), csrf='wrong')._send.call_args.args[0], 403)
        self.assertEqual(post('/create', dict(settings={}, text=DEFAULT_TEXT), cookie=None)._send.call_args.args[0], 401)
        result = post('/create', dict(settings=dict(level='profi'), text=DEFAULT_TEXT))
        self.assertEqual(result._send.call_args.args[0], 201)
        task = json.loads(result._send.call_args.args[1])
        self.assertEqual(task['level_label'], 'Profi')
        self.assertTrue(task['help'])
        self.assertEqual(post('/families', dict(identity=task['id'], ipv4=True, ipv6=False, text=task['text']))._send.call_args.args[0], 400)
        self.assertEqual(post('/standard', dict(settings={}, text=DEFAULT_TEXT))._send.call_args.args[0], 403)
        for kind, magic in (('pdf', b'%PDF-'), ('zip', b'PK')):
            handler = self.handler('/api/cli-exercises/export/' + kind + '/' + task['id'] + '/1', sid=sid)
            handler.server.cli_exercises = store
            handler.do_GET()
            self.assertEqual(handler._send.call_args.args[0], 200)
            self.assertTrue(handler._send.call_args.args[1].startswith(magic))
            self.assertIn('Profi', dict(handler._send.call_args.kwargs['headers'])['Content-Disposition'])
        listing = self.handler('/api/cli-exercises/page/1', sid=sid)
        listing.server.cli_exercises = store
        listing.do_GET()
        self.assertEqual(json.loads(listing._send.call_args.args[1])['pagination']['total'], 1)


if __name__ == '__main__':
    unittest.main()
