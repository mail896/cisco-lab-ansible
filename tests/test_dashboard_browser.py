"""Browser workflow against intercepted fixtures; no network or LAB actions."""
import json
import re
from pathlib import Path
import unittest
from urllib.parse import urlparse

try:
    from playwright.sync_api import sync_playwright, expect
except ImportError:
    sync_playwright = None

from tests.test_dashboard_intake import device

ROOT = Path(__file__).resolve().parents[1]
BASE = 'http://lab.test/cisco-lab'


@unittest.skipUnless(sync_playwright, 'Optional project-local Playwright is not on PYTHONPATH')
class BrowserWorkflowTests(unittest.TestCase):
    def test_named_playbook_group_save_sort_execute_log_and_fresh_selection(self):
        self.page.goto(BASE+'/playbooks')
        self.page.locator('#group-name').fill('Netzwerkunterricht')
        self.page.locator('#group-modules input[value="serial"]').check()
        self.page.locator('#group-modules input[value="firefox"]').check()
        self.page.get_by_role('button',name='Nach oben: Firefox-Richtlinien',exact=True).click()
        self.page.locator('#group-save').click()
        expect(self.page.locator('#group-message')).to_contain_text('Gruppe gespeichert')
        self.assertEqual(self.groups['groups'][0]['steps'],['firefox','serial'])
        expect(self.page.locator('#group-start')).to_be_disabled()
        self.page.locator('#group-targets input[value="LAB-08B"]').check()
        self.page.locator('#group-start').click()
        expect(self.page.locator('#catalog-confirm-text')).to_contain_text('Netzwerkunterricht')
        self.page.locator('#catalog-confirm button[value="start"]').click()
        run=self.page.locator('#group-runs details').first
        expect(run).to_contain_text('Netzwerkunterricht');run.locator('summary').click()
        run.get_by_role('button',name='Protokoll',exact=True).click()
        expect(self.page.locator('#group-output')).to_contain_text('Fixture: group complete')
        self.page.locator('#group-log-close').click();expect(self.page.locator('#group-log')).to_be_hidden()
        started=[payload for path,payload in self.calls if path=='/api/playbook-groups/start']
        self.assertEqual(started[0]['targets'],['LAB-08B'])
        self.ready=['LAB-08B'];self.accepted=True
        self.page.goto(BASE+'/installationsfolge')
        self.page.locator('#fresh-group').select_option('a'*32)
        self.page.locator('#fresh-targets input[data-host="LAB-08B"]').check()
        self.page.locator('#fresh-start').click()
        expect(self.page.locator('#catalog-confirm-text')).to_contain_text('Netzwerkunterricht')
        self.page.locator('#catalog-confirm button[value="start"]').click()
        expect(self.page.locator('#fresh-status')).to_contain_text('Derzeit ist kein frisch')
        self.assertEqual(self.fresh_group,'a'*32)
        self.assertEqual(self.errors,[])

    def test_reader_can_view_group_but_cannot_edit_or_execute(self):
        self.role='reader'
        self.groups['groups']=[dict(id='a'*32,name='Netzwerkunterricht',steps=['serial'])]
        self.page.goto(BASE+'/playbooks')
        self.page.locator('#group-choice').select_option('a'*32)
        expect(self.page.locator('#group-name')).to_have_value('Netzwerkunterricht')
        for selector in ('#group-name','#group-save','#group-delete','#group-start','#group-modules input'):
            expect(self.page.locator(selector).first).to_be_disabled()
        self.assertFalse(any(payload is not None and path.startswith('/api/playbook-groups/') for path,payload in self.calls))
        self.assertEqual(self.errors,[])

    def setUp(self):
        self.playwright = sync_playwright().start()
        self.addCleanup(self.playwright.stop)
        self.browser = self.playwright.chromium.launch(headless=True)
        self.addCleanup(self.browser.close)
        self.context = self.browser.new_context(viewport={'width': 1360, 'height': 1000})
        self.page = self.context.new_page()
        self.errors = []
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))
        self.calls = []
        self.activity = dict(busy=False)
        self.reads = []; self.runs = []; self.ready = []; self.accepted = False
        self.read_failure = None
        self.maintenance_hosts = ['LAB-08A']
        self.maintenance_can_start = False
        self.maintenance_busy = False
        self.role = 'admin'
        self.presence=dict(devices={'LAB-08A':dict(state='online',rtt_ms=1),
                                    'LAB-08B':dict(state='unreachable',rtt_ms=None)},
                           checked_at='2026-11-08T12:44:00+00:00',age_seconds=0,interval_seconds=30,refreshing=False)
        self.software = dict(catalog=dict(count=12345,release='Debian 13',architecture='amd64',source_host='LAB-08A',lists_at='2026-11-08T10:00:00+01:00',indexing=False,error=''),profiles=[],hosts=['LAB-08A'],busy=False,runs=[],inventory={},inventory_refreshing=False)
        self.classroom=dict(clients={f'LAB-{number:02d}{side}':dict(state='unmanaged') for number in range(1,9) for side in 'AB'},
            checked_at='2026-11-08T20:00:00+01:00',age_seconds=0,interval_seconds=60,refreshing=False,error='')
        self.classroom['clients']['LAB-08A']=dict(state='up',admin_up=True,interface='lab0',
            users=[dict(account='mara.mustermann',kind='iserv',locked=False)],
            hardware=dict(manufacturer='Realtek',model='RTL8153 Gigabit Ethernet',usb_id='0bda:8153',driver='r8152'),
            ipv4=[dict(address='192.0.2.2/24',scope='global')],ipv6=[dict(address='fe80::2/64',scope='link'),dict(address='2001:db8::2/64',scope='global')],
            routes4=[dict(destination='198.51.100.0/24',gateway='192.0.2.1',table='main',metric=50),dict(destination='192.0.2.2/32',gateway=None,table='local',type='local')],routes6=[],
            observed_at='2026-11-08T20:00:00+01:00')
        self.classroom['clients']['LAB-01A']=dict(self.classroom['clients']['LAB-08A'],state='down',admin_up=False,
            users=[dict(account='jona.beispiel',kind='iserv',locked=True)],
            hardware=dict(manufacturer='ASIX',model='AX88772A',usb_id='0b95:7720',driver='asix'))
        self.classroom['clients']['LAB-02A']=dict(state='unreachable',error='Nicht erreichbar')
        self.backup_transfer_available = True
        self.room_access = None
        self.backup_cleanup = None
        self.cleanup_candidates = []
        self.appearance = dict(folder='LAB-Fixture',images=['custom.png'],selected=dict(desktop='',lock=''),
                               previews={key:dict(active='a'*64,standard='a'*64) for key in ('desktop','lock')},
                               image_previews={'custom.png':'b'*64})
        self.appearance_approval = None
        self.pilot_jobs=[dict(id='firefox',label='Firefox',checks=dict(homepage='Startseite erscheint.',tabs='Neue Tabs sind leer.',policies='Richtlinien sind fehlerfrei.')),
                         dict(id='appearance',label='Desktop- und Sperrbild',checks=dict(desktop='Desktop sichtbar.',lock='Sperrbild sichtbar.',greeter='Anmeldebild sichtbar.'))]
        self.pilot_review_error=None
        from dashboard.provision import ENTRIES
        self.catalog_entries={key:dict(value) for key,value in ENTRIES.items()}
        self.catalog_approvals={}
        from dashboard.playbook_groups import MODULES
        self.groups = dict(groups=[], modules=list(MODULES.values()), profiles=[], hosts=['LAB-01A','LAB-08B'], busy=False, runs=[])
        self.fresh_group = ''
        self.client_report=dict(timestamp='2026-11-08T17:00:00+01:00',window=False,refreshing=False,refresh_error=None,
            counts=dict(install_if_still_free=1,unknown=15),clients=[dict(host=f'LAB-{number:02d}{side}',
                action='install_if_still_free' if (number,side)==(8,'A') else 'unknown',updates=2,removals=0,
                cleanup=False,patch='patched' if (number,side)==(8,'A') else 'unknown',apt_enabled=False)
                for number in range(1,9) for side in 'AB'])
        self.histories = {}; self.page_calls = []
        self.context.route('**/*', self.route)

    def fresh(self):
        return dict(hosts=self.ready, hardware=[], runs=self.runs, veyon=True, busy=False, parallelism=4, playbook_groups=self.groups['groups'])

    def route(self, route):
        request = route.request
        url = urlparse(request.url)
        if url.netloc != 'lab.test' or not url.path.startswith('/cisco-lab'):
            self.errors.append('Unexpected network request: ' + request.url); route.abort(); return
        path = url.path[len('/cisco-lab'):]
        history_page = None
        paged = re.fullmatch(r'(.+)/page/(\d+)', path)
        if paged:
            path, number = paged.groups(); history_page = int(number)
            self.page_calls.append((path, history_page))
        if path.startswith('/api/'):
            payload = (dict(name=request.headers['x-lab-image-name' if path=='/api/appearance/upload' else 'x-lab-package-name'],size=len(request.post_data_buffer)) if path in ('/api/appearance/upload','/api/software/package/upload')
                       else request.post_data_json) if request.method == 'POST' else None
            self.calls.append((path, payload))
            if path.startswith('/api/appearance/preview/'):
                import base64
                route.fulfill(content_type='image/png',body=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j5ioAAAAASUVORK5CYII='))
                return
            if path.startswith('/api/backup/download/'):
                route.fulfill(status=200,content_type='application/zip',headers={'Content-Disposition':'attachment; filename="fixture.zip"'},body=b'fixture download')
                return
            if path=='/api/classroom':data=self.classroom
            elif path == '/api/software': data=self.software
            elif path == '/api/software/package/upload':
                data=dict(name='lab-example',version='1.0',architecture='all',description='Own LAB example',size=payload['size'],sha256='d'*64)
                self.software.setdefault('own_packages',[]).append(data)
            elif path.startswith('/api/software/run/'):
                data=next(row for row in self.software['runs'] if row['id']==path.rsplit('/',1)[1])
            elif path == '/api/software/teacher/check':
                self.software['teacher']=dict(ready=True,error='');self.software['hosts'].append('LAB-Teacher')
                data=self.software['teacher']
            elif path.startswith('/api/software/search/'):
                data=dict(packages=[dict(name='gimp',version='3.0',description='Image editor',section='graphics',bytes=1000000,
                    installed={host:'3.0' for host in self.software['inventory']},installed_at={host:row['checked_at'] for host,row in self.software['inventory'].items()})],pagination=dict(page=1,pages=1,total=1,page_size=10))
            elif path=='/api/software/inventory':
                self.software['inventory']={host:dict(checked_at='2026-11-08T20:00:00+01:00',count=1234,error='',refreshing=False) for host in payload['targets']};data=dict(started=True)
            elif path == '/api/software/profile':
                data=dict(payload,id=payload['id'] or 'a'*32,approved=None)
                self.software['profiles']=[data]
            elif path == '/api/software/delete':self.software['profiles']=[];data=dict(deleted=True)
            elif path == '/api/software/plan':
                data=dict(id='b'*32,kind='plan',status='succeeded',packages=payload['packages'],targets=payload['targets'],profile_id=payload['profile_id'],started='2026-11-08T17:00:00+01:00',results={host:dict(status='succeeded',changes=[dict(name='gimp',version='3.0',action='install')],already=[],download_bytes=1000000,removals=[]) for host in payload['targets']})
                self.software['runs'].insert(0,data)
            elif path == '/api/software/start':
                plan=next(row for row in self.software['runs'] if row['id']==payload['run_id']);plan['consumed']=True
                data=dict(plan,id='c'*32,kind='install');self.software['runs'].insert(0,data)
            elif path == '/api/software/review':
                data=self.software['profiles'][0];data['approved']='fixture'
            elif path.startswith('/api/software/log/'):data=dict(output='Fixture: software installed')
            elif path == '/api/activity': data = self.activity
            elif path == '/api/intake':
                data = dict(clients=[dict(host=host, address='203.0.113.16',
                                         status='managed' if host == 'LAB-08A' else 'ready' if host in self.ready else 'new')
                                     for host in ('LAB-08B', 'LAB-01A', 'LAB-08A')],
                            reads=self.reads, busy=False, pilot_accepted=self.accepted)
            elif path == '/api/fresh': data = self.fresh()
            elif path.startswith('/api/fresh/run/'):
                data=next(row for row in self.histories.get('/api/fresh',self.runs) if row['id']==path.rsplit('/',1)[-1])
            elif path == '/api/state': data=self.client_report
            elif path == '/api/refresh': self.client_report['refreshing']=True;data=dict(started=True)
            elif path == '/api/teacher/status': data = dict(configuration=dict(enabled=True), runs=[])
            elif path == '/api/presence':
                data = self.presence
            elif path == '/api/logins':
                data = dict(records=[dict(time='2026-11-08T12:40:00+00:00',account='student.test',name='Testschueler',
                                         outcome='denied',reason='not_collegium',role=None)],retention_days=90,limit=200)
            elif path == '/api/intake/read':
                self.assertTrue(payload['image_confirmed'])
                self.assertNotIn('fingerprint', payload)
                run = dict(id='a'*32, host=payload['host'], status='succeeded', device=device(), fingerprint='SHA256:'+'a'*43)
                if self.read_failure:
                    run.update(status='failed', error=self.read_failure)
                self.reads = [run]; data = run
            elif path == '/api/intake/read-many':
                self.assertTrue(payload['image_confirmed'])
                self.assertEqual(payload['ssh_password'], 'test-ephemeral')
                self.reads = [dict(id=format(index+1, '032x'), host=host, status='succeeded',
                    device=device(host), fingerprint='SHA256:'+'a'*43) for index, host in enumerate(payload['hosts'])]
                if self.read_failure:
                    self.reads[-1].update(status='failed',error=self.read_failure)
                data = dict(runs=self.reads)
            elif path == '/api/intake/setup-many':
                self.assertEqual(payload['student'],'student')
                self.assertEqual(payload['ssh_password'], 'test-ephemeral')
                self.assertEqual(payload['sudo_password'], 'test-ephemeral')
                self.ready = [run['host'] for run in self.reads if run['id'] in {row['run_id'] for row in payload['targets']}]
                data = dict(runs=[dict(id=format(index+10,'032x'),host=host) for index,host in enumerate(self.ready)])
            elif path == '/api/intake/setup':
                self.assertEqual(payload['student'],'student')
                self.assertEqual(payload['ssh_password'], 'test-ephemeral')
                self.assertEqual(payload['sudo_password'], 'test-ephemeral')
                self.assertEqual(payload['lab_interface'], 'enx001122334466')
                self.ready = ['LAB-08B']; data = dict(id='b'*32)
            elif path == '/api/fresh/start':
                self.fresh_group = payload.get('group_id', '')
                self.assertEqual(payload['targets'], [dict(host='LAB-08B', veyon=False)])
                self.ready = []
                self.runs = [dict(id='c'*32, host='LAB-08B', status='succeeded', phase='maintenance',
                                 started='2026-11-09T10:00:00+01:00', finished='2026-11-09T10:01:00+01:00')]
                data = dict(runs=self.runs)
            elif path == '/api/intake/accept': self.accepted = True; data = dict(accepted=True)
            elif path.startswith('/api/fresh/log/'): data = dict(output='Fixture: baseline complete')
            elif path == '/api/room':
                from dashboard.room import ACTIONS, SCOPES, CAPABILITIES, TARGET_CAPABILITIES
                data = dict(actions=ACTIONS, scopes=SCOPES, capabilities=CAPABILITIES, target_capabilities=TARGET_CAPABILITIES, runs=[])
                if self.room_access is not None:
                    data.update(access=self.room_access,target_capabilities={host:list(ACTIONS) if self.room_access.get(host) else ['wake'] for host in TARGET_CAPABILITIES})
            elif path == '/api/room/start':
                data = dict(id='d'*32)
                self.histories['/api/room'] = [dict(id='d'*32, action=payload['action'], scope='selection', targets=payload['targets'],
                    status='finished', results={host:'Fixture' for host in payload['targets']}, started='2026-11-08T12:00:00+01:00')]
            elif path == '/api/backup': data = dict(runs=[],mode='apps',transfer_available=self.backup_transfer_available)
            elif path == '/api/backup/start':
                data = dict(id='e'*32)
                self.histories['/api/backup'].insert(0, dict(id='e'*32,status='running',targets=payload['targets'],started='2026-11-08T15:00:00+01:00'))
            elif path == '/api/backup/confirm':
                for run in self.histories['/api/backup']:
                    if run['id'] == payload['run_id']: run['confirmed_good_at'] = '2026-11-08T15:00:00+01:00'
                data = dict(confirmed=True)
            elif path == '/api/backup/cleanup' and payload is None:
                data = dict(candidates=self.cleanup_candidates,protected=['f'*32],skipped=[],token='a'*64,
                            bytes={target:sum(row['size'] for row in self.cleanup_candidates if row.get(target)) for target in ('apps','mac')})
            elif path == '/api/backup/cleanup':
                self.backup_cleanup = dict(id='f'*32,status='running',total=len(self.cleanup_candidates),processed=0,removed=0,bytes=dict(apps=0,mac=0),errors=[])
                data = self.backup_cleanup
            elif path == '/api/pilot': data = dict(runs=[],hosts=['LAB-08A','LAB-08B'],jobs=self.pilot_jobs)
            elif path == '/api/pilot/plan':
                data=dict(host=payload['host'],job=payload['job'],revision='a'*64,token='b'*32,tasks=['Fixture: Voraussetzungen prüfen','Fixture: Einstellungen übernehmen'])
                if payload['job']=='appearance':data['images']=dict(desktop='Fixture-Desktop.png',lock='Fixture-Sperre.jpg')
            elif path == '/api/pilot/start':
                data=dict(id='c'*32,host=payload['host'],job=payload['job'],status='running',started='2026-11-08T17:00:00+01:00',revision=payload['revision'])
                self.histories.setdefault('/api/pilot',[]).insert(0,data)
            elif path.startswith('/api/pilot/log/'):data=dict(output='Fixture: pilot output')
            elif path == '/api/pilot/review':
                if self.pilot_review_error:
                    route.fulfill(status=400,content_type='application/json',body=json.dumps(dict(error=self.pilot_review_error)));return
                data=next(row for row in self.histories['/api/pilot'] if row['id']==payload['run_id'])
                data['review']=dict(decision=payload['decision'],checks=payload['checks'],note=payload['note'],reviewed='2026-11-08T17:05:00+01:00')
            elif path == '/api/appearance': data = self.appearance
            elif path == '/api/appearance/select':
                self.appearance['selected']=payload
                for key in ('desktop','lock'):self.appearance['previews'][key]['active']='b'*64 if payload[key] else 'a'*64
                self.appearance_approval=None
                data=self.appearance
            elif path == '/api/appearance/upload':
                from urllib.parse import unquote
                name=unquote(payload['name']);self.appearance['images'].append(name);self.appearance['image_previews'][name]='b'*64
                data=dict(uploaded=name,choices=self.appearance)
            elif path == '/api/appearance/rollout': data = dict(runs=[],approval=self.appearance_approval,targets=['LAB-08A'])
            elif path == '/api/appearance/rollout/start':
                data=dict(id='c'*32)
                self.histories['/api/appearance/rollout']=[dict(id='c'*32,status='running',started='2026-11-08T17:00:00+01:00',targets=['LAB-08A'],results={},active=['LAB-08A'],revision='a'*64)]
            elif path.startswith('/api/appearance/rollout/log/'): data=dict(output='Fixture: image deployed')
            elif path.startswith('/api/rollout/state/'):
                job=path.rsplit('/',1)[-1];data=dict(runs=[],approval=self.catalog_approvals.get(job),targets=['LAB-08A'])
            elif path == '/api/rollout/start':
                row=dict(id='f'*32,status='running',started='2026-11-08T17:00:00+01:00',targets=['LAB-08A'],results={},active=['LAB-08A'],revision='a'*64,pending=['LAB-08A'])
                self.histories.setdefault('/api/rollout/state/'+payload['job'],[]).insert(0,row);data=row
            elif path == '/api/playbook-groups': data=self.groups
            elif path == '/api/playbook-groups/save':
                row = dict(id=payload['identity'] or 'a'*32, name=payload['name'], steps=payload['steps'])
                self.groups['groups']=[group for group in self.groups['groups'] if group['id']!=row['id']]+[row];data=row
            elif path == '/api/playbook-groups/delete':
                self.groups['groups']=[group for group in self.groups['groups'] if group['id']!=payload['identity']];data=dict(deleted=True)
            elif path == '/api/playbook-groups/start':
                group=next(group for group in self.groups['groups'] if group['id']==payload['group_id'])
                steps=[dict(id=step,label=step) for step in group['steps']]
                run=dict(id='b'*32,group=dict(name=group['name'],steps=steps),status='succeeded',started='2026-11-09T13:00:00+01:00',
                         targets=payload['targets'],results={host:dict(message='Alle Bausteine abgeschlossen.') for host in payload['targets']},active={})
                self.groups['runs'].insert(0,run);data=run
            elif path.startswith('/api/playbook-groups/log/'): data=dict(output='Fixture: group complete')
            elif path == '/api/playbooks': data=self.catalog_entries
            elif path.startswith('/api/playbooks/'):
                job=path.rsplit('/',1)[-1];data=dict(content='# Fixture '+job,entry=self.catalog_entries[job])
            elif path == '/api/maintenance-hosts': data = dict(hosts=self.maintenance_hosts)
            elif path.startswith('/api/maintenance/') and path != '/api/maintenance/start':
                data = dict(available=True,active='inactive',output='Fixture',can_start=self.maintenance_can_start)
            elif path == '/api/maintenance/start': data = dict(started=True)
            elif path == '/api/maintenance-runs': data = dict(runs=[],enrolled=['LAB-08A'],busy=self.maintenance_busy,teacher=dict(configuration=dict(enabled=True),runs=[]))
            elif path == '/api/teacher/runs': data = dict(runs=[])
            else: self.errors.append('Unexpected API: '+path); data = dict(error='Unexpected fixture API')
            if path in self.histories: data['records' if path == '/api/logins' else 'runs'] = self.histories[path]
            if path == '/api/fresh' and history_page is None:
                rows=data['runs']
                data['runs']=[row for row in rows if row['status'] in ('running','queued')] + [
                    row for row in rows if row['status'] not in ('running','queued')][:30]
            if path == '/api/backup':
                rows=data['runs'];data['latest']=rows[0] if rows else None
                data['cleanup']=self.backup_cleanup
                data['busy']=any(row['status'] in ('running','queued') for row in rows) or bool(self.backup_cleanup and self.backup_cleanup['status']=='running')
                data['locations']={}
                for target,field,folder in [('apps','data_copy','/var/lib/cisco-lab/backup_data'),('mac','copied','/Volumes/Repo/cisco-lab'),('download','download_ready_at','Downloads im Browser')]:
                    last=next((row for row in rows if row.get(field) and not row.get('pruned_at')),None)
                    data['locations'][target]=dict(path=folder,last=dict(time=last.get('copied_at' if target=='mac' else 'download_ready_at' if target=='download' else 'local_saved_at',last['started'])) if last else None)
            if history_page is not None:
                from dashboard.pagination import paginate
                data = paginate(data,history_page,'records' if path == '/api/logins' else 'runs',fresh=path=='/api/fresh')
            route.fulfill(status=200, content_type='application/json', body=json.dumps(data)); return
        name = path.lstrip('/')
        from dashboard.server import PUBLIC_THEME_ASSETS,PUBLIC_AUTH_ASSETS
        if path in PUBLIC_THEME_ASSETS:name=PUBLIC_THEME_ASSETS[path][0]
        if path in PUBLIC_AUTH_ASSETS:name=PUBLIC_AUTH_ASSETS[path][0]
        if path in ('/', '/schuelerclients', '/installationsfolge', '/sammelaktionen', '/playbooks', '/anmeldungen', '/pilotdeploys', '/backup', '/desktopbilder', '/wartung','/repo-software','/unterricht','/aufgaben'): name = 'index.html'
        file = ROOT / 'dashboard' / name
        if not file.is_file(): route.fulfill(status=404); return
        if name == 'index.html':
            body = file.read_text().replace('__BASE_PATH__', '/cisco-lab').replace('__CONTROLLER_LABEL__', 'APP-Server')
            from dashboard.server import FOOTER,THEME_SCRIPT,THEME_STYLESHEET,THEME_PICKER
            body=body.replace('__THEME_SCRIPT__',THEME_SCRIPT).replace('__THEME_STYLESHEET__',THEME_STYLESHEET).replace('__THEME_PICKER__',THEME_PICKER)
            body = body.replace('__ROLE__', self.role).replace('__CSRF__', 'fixture-csrf')
            body = body.replace('__SESSION_LABEL__', 'Testperson · '+('Lesezugriff' if self.role == 'reader' else 'Administrator'))
            body = body.replace('__SESSION_ACTION__', '').replace('__FOOTER__', FOOTER)
            route.fulfill(content_type='text/html', body=body)
        else:
            kind = 'text/javascript' if name.endswith('.js') else 'text/css' if name.endswith('.css') else 'image/svg+xml'
            route.fulfill(content_type=kind, body=file.read_bytes())

    def test_theme_follows_system_and_remembers_explicit_choice_across_pages(self):
        self.page.emulate_media(color_scheme='dark');self.page.goto(BASE+'/repo-software')
        root=self.page.locator('html');picker=self.page.locator('[data-theme-select]')
        expect(root).to_have_attribute('data-theme','dark');expect(picker).to_have_value('system')
        self.assertEqual(self.page.locator('html').evaluate('(node)=>getComputedStyle(node).backgroundColor'),'rgb(16, 24, 36)')
        picker.select_option('light');expect(root).to_have_attribute('data-theme','light')
        self.page.goto(BASE+'/unterricht');expect(root).to_have_attribute('data-theme','light')
        expect(picker).to_have_value('light');picker.select_option('system')
        expect(root).to_have_attribute('data-theme','dark')
        self.page.emulate_media(color_scheme='light');expect(root).to_have_attribute('data-theme','light')
        picker.select_option('dark');self.page.reload();expect(root).to_have_attribute('data-theme','dark')
        self.assertFalse(any(payload is not None for _,payload in self.calls));self.assertFalse(self.errors,self.errors)

    def test_theme_syncs_tabs_and_stays_usable_when_browser_storage_is_blocked(self):
        self.page.goto(BASE+'/repo-software')
        second=self.context.new_page();second.goto(BASE+'/backup')
        self.page.locator('[data-theme-select]').select_option('dark')
        expect(second.locator('html')).to_have_attribute('data-theme','dark')
        self.page.add_init_script("Object.defineProperty(window,'localStorage',{get(){throw new DOMException('blocked','SecurityError')}})")
        self.page.reload();self.page.locator('[data-theme-select]').select_option('dark')
        expect(self.page.locator('html')).to_have_attribute('data-theme','dark');self.assertFalse(self.errors,self.errors)

    def test_dark_surfaces_dialogs_forms_status_and_reader_theme_control(self):
        self.role='reader';self.page.emulate_media(color_scheme='dark');self.page.goto(BASE+'/repo-software')
        expect(self.page.locator('[data-theme-select]')).to_be_enabled()
        self.assertEqual(self.page.locator('.software-selection').evaluate('(node)=>getComputedStyle(node).backgroundColor'),'rgb(25, 37, 54)')
        self.assertEqual(self.page.locator('#software-search').evaluate('(node)=>getComputedStyle(node).backgroundColor'),'rgb(19, 32, 50)')
        self.page.evaluate("confirmCatalog('Fixture confirmation',()=>{},'Test','Testdialog')")
        expect(self.page.locator('#catalog-confirm')).to_be_visible()
        self.assertEqual(self.page.locator('#catalog-confirm').evaluate('(node)=>getComputedStyle(node).backgroundColor'),'rgb(25, 37, 54)')
        self.page.locator('#catalog-confirm button[value="cancel"]').click()
        self.page.set_viewport_size(dict(width=390,height=844))
        self.assertTrue(self.page.evaluate('document.documentElement.scrollWidth <= innerWidth'));self.assertFalse(self.errors,self.errors)

    def test_classroom_models_addresses_routes_and_local_filter_are_read_only(self):
        self.role='reader';self.page.goto(BASE+'/unterricht')
        expect(self.page.locator('[data-classroom-host]')).to_have_count(16)
        card=self.page.locator('[data-classroom-host="LAB-08A"]')
        for text in ('Link aktiv','Realtek','RTL8153','0bda:8153','192.0.2.2/24','2001:db8::2/64','Link-lokal','198.51.100.0/24 → 192.0.2.1'):
            expect(card).to_contain_text(text)
        expect(self.page.locator('#classroom-count-link')).to_have_text('1')
        expect(self.page.locator('#classroom-count-ipv6')).to_have_text('2')
        details=card.locator('.classroom-details');expect(details).not_to_have_attribute('open','')
        before=sum(path=='/api/classroom' for path,_ in self.calls)
        details.locator(':scope > summary').click()
        local=card.locator('[data-classroom-fold="LAB-08A-Routen IPv4"]')
        local.locator('summary').click()
        self.assertEqual(sum(path=='/api/classroom' for path,_ in self.calls),before)
        self.page.evaluate('loadClassroom()');expect(details).to_have_attribute('open','');expect(local).to_have_attribute('open','')
        details.locator(':scope > summary').click();self.page.evaluate('loadClassroom()')
        expect(details).not_to_have_attribute('open','')
        before=sum(path=='/api/classroom' for path,_ in self.calls)
        self.page.locator('#classroom-search').fill('08')
        expect(self.page.locator('[data-classroom-host]')).to_have_count(2)
        self.assertEqual(sum(path=='/api/classroom' for path,_ in self.calls),before)
        self.page.set_viewport_size(dict(width=390,height=844))
        self.assertTrue(self.page.evaluate('document.documentElement.scrollWidth <= innerWidth'))
        self.assertFalse(any(payload is not None for _,payload in self.calls));self.assertFalse(self.errors,self.errors)

    def test_physical_islands_and_seated_ab_order_are_shared_without_extra_requests(self):
        expected=[['06','07'],['03','02'],['01','08'],['05','04']]
        for path,container,selector in [
            ('/sammelaktionen','#room-targets','input[data-room-host]'),
            ('/repo-software','#software-targets','input[data-software-host]'),
            ('/unterricht','#classroom-clients','[data-classroom-host]')]:
            self.page.goto(BASE+path)
            islands=self.page.locator(container+' [data-room-island]');expect(islands).to_have_count(4)
            self.assertEqual(islands.evaluate_all("nodes=>nodes.map(node=>[...node.querySelectorAll('[data-room-bank]')].map(bank=>bank.dataset.roomBank))"),expected)
            for top,bottom in expected:
                for team,sides in [(top,'BA'),(bottom,'AB')]:
                    bank=self.page.locator(container+' [data-room-bank="'+team+'"]')
                    names=bank.locator(selector).evaluate_all("nodes=>nodes.map(node=>node.dataset.softwareHost||node.dataset.classroomHost||node.value)")
                    self.assertEqual(names,['LAB-'+team+side for side in sides])
            expect(self.page.locator(container+' .lab-room-island-label').first).to_have_text('Eingang links')
            expect(self.page.locator(container+' [data-room-island="lower-right"] > .lab-room-island-label')).to_have_text('Fenster rechts')
            for width in (1440,390):
                self.page.set_viewport_size(dict(width=width,height=1000))
                self.assertTrue(self.page.evaluate('document.documentElement.scrollWidth <= innerWidth'),path)
                for top,bottom in expected:
                    upper=self.page.locator(container+' [data-room-bank="'+top+'"]')
                    lower=self.page.locator(container+' [data-room-bank="'+bottom+'"]')
                    self.assertLess(upper.bounding_box()['y'],lower.bounding_box()['y'])
                    for bank,side in ((upper,'north'),(lower,'south')):
                        expect(bank).to_have_attribute('data-room-side',side)
                        seats=bank.locator(selector)
                        first,second=seats.nth(0).bounding_box(),seats.nth(1).bounding_box()
                        self.assertLess(first['x'],second['x'])
                        self.assertAlmostEqual(first['y'],second['y'],delta=1)
            self.page.set_viewport_size(dict(width=1440,height=1000))
        self.page.goto(BASE+'/')
        bank=self.page.locator('#overview-devices [data-room-bank="08"]')
        expect(bank.locator('.device-card')).to_have_count(2)
        self.assertEqual(bank.locator('.device-card').evaluate_all('nodes=>nodes.map(node=>node.dataset.roomHost)'),['LAB-08A','LAB-08B'])
        teacher=self.page.locator('#overview-devices .lab-room-teacher')
        expect(teacher).to_contain_text('neben den Pilotrechnern')
        self.assertEqual(teacher.locator('.device-card').count(),1)
        self.assertFalse(any(payload is not None for _,payload in self.calls));self.assertFalse(self.errors,self.errors)

    def test_classroom_users_stay_at_their_seats_and_clear_after_failed_refresh(self):
        self.role='reader'
        self.classroom['clients']['LAB-03B']=dict(state='missing',users=[dict(account='alexander.langernachname',kind='iserv',locked=False)])
        self.page.goto(BASE+'/unterricht')
        for host,name in [('LAB-03B','alexander.langernachname'),('LAB-01A','jona.beispiel'),('LAB-08A','mara.mustermann')]:
            label=self.page.locator('[data-occupant-host="'+host+'"]')
            expect(label).to_have_text(name)
            chair=label.locator('xpath=ancestor::*[@data-room-bank]')
            bank=chair.bounding_box();person=label.bounding_box()
            if chair.get_attribute('data-room-side')=='north':self.assertLess(person['y']+person['height'],bank['y'])
            else:self.assertGreater(person['y'],bank['y']+bank['height'])
        expect(self.page.locator('[data-occupant-host="LAB-01A"]')).to_have_attribute('title',re.compile('gesperrt'))
        self.page.locator('#classroom-search').fill('03B')
        expect(self.page.locator('.lab-room-occupant')).to_have_count(1)
        expect(self.page.locator('.lab-room-occupant')).to_have_attribute('data-room-seat','1')
        self.page.set_viewport_size(dict(width=390,height=844))
        self.assertTrue(self.page.evaluate('document.documentElement.scrollWidth <= innerWidth'))
        self.page.locator('#classroom-search').fill('')
        self.classroom['clients']['LAB-08A']=dict(state='unreachable')
        self.page.evaluate('loadClassroom()')
        expect(self.page.locator('[data-occupant-host="LAB-08A"]')).to_have_text('')
        expect(self.page.locator('[data-occupant-host="LAB-08A"]')).to_have_attribute('title','LAB-08A · Anmeldung derzeit nicht bekannt')
        self.classroom['clients']['LAB-08A']=dict(state='missing',users=[])
        self.page.evaluate('loadClassroom()')
        expect(self.page.locator('[data-occupant-host="LAB-08A"]')).to_have_attribute('title','LAB-08A · Niemand grafisch angemeldet')
        self.assertFalse(any(payload is not None for _,payload in self.calls));self.assertFalse(self.errors,self.errors)

    def test_classroom_polling_pauses_when_hidden_or_manually_stopped(self):
        self.page.clock.install();self.page.goto(BASE+'/unterricht')
        expect(self.page.locator('[data-classroom-host]')).to_have_count(16)
        count=lambda:sum(path=='/api/classroom' for path,_ in self.calls)
        self.page.wait_for_function('() => !classroomLoading && !presenceLoading')
        expect(self.page.locator('#classroom-stamp')).to_contain_text('60 Sekunden')
        before=count();self.page.clock.run_for(30000);self.assertEqual(count(),before)
        self.page.clock.run_for(30500)
        self.page.wait_for_function('() => !classroomLoading && !presenceLoading')
        self.assertEqual(count(),before+1)
        before=count()
        self.page.evaluate("Object.defineProperty(document,'hidden',{configurable:true,get:()=>true});document.dispatchEvent(new Event('visibilitychange'))")
        self.page.clock.fast_forward(65000);self.assertEqual(count(),before)
        self.page.evaluate("Object.defineProperty(document,'hidden',{configurable:true,get:()=>false});document.dispatchEvent(new Event('visibilitychange'))")
        expect(self.page.locator('#classroom-status')).to_have_text('Live')
        self.page.wait_for_function('() => !classroomLoading && !presenceLoading')
        self.assertEqual(count(),before+1)
        self.page.locator('#classroom-pause').click();before=count()
        presence_before=sum(path=='/api/presence' for path,_ in self.calls)
        self.page.clock.fast_forward(65000);self.assertEqual(count(),before)
        self.assertEqual(sum(path=='/api/presence' for path,_ in self.calls),presence_before)
        expect(self.page.locator('#classroom-status')).to_have_text('Pausiert')
        expect(self.page.locator('.lab-room-presence-stamp:visible')).to_contain_text('pausiert')
        self.page.locator('#classroom-pause').click();self.page.wait_for_function('() => !classroomLoading && !presenceLoading')
        self.assertEqual(sum(path=='/api/presence' for path,_ in self.calls),presence_before+1)
        self.assertEqual(count(),before+1);self.assertFalse(self.errors,self.errors)

    def test_software_profile_preview_exact_targets_install_review_and_close_log(self):
        self.page.goto(BASE+'/repo-software')
        self.page.locator('#software-search').fill('gimp')
        expect(self.page.locator('#software-results')).to_contain_text('gimp')
        expect(self.page.locator('#software-install')).to_be_disabled()
        self.page.locator('[data-software-add="gimp"]').click()
        self.page.locator('#software-profile-name').fill('Grafikunterricht')
        expect(self.page.locator('#software-profile-baseline')).to_be_disabled()
        self.page.locator('#software-profile-save').click()
        expect(self.page.locator('#software-status')).to_have_text('Softwareprofil gespeichert.')
        expect(self.page.locator('#software-export')).to_be_visible()
        self.page.locator('[data-software-host="LAB-08A"]').check()
        expect(self.page.locator('[data-software-host="LAB-08B"]')).to_be_disabled()
        self.page.locator('#software-plan').click()
        expect(self.page.locator('#software-install')).to_be_enabled()
        self.assertIn(('/api/software/plan',dict(packages=['gimp'],targets=['LAB-08A'],profile_id='a'*32)),self.calls)
        self.page.locator('#software-install').click()
        expect(self.page.locator('#catalog-confirm-text')).to_contain_text('gimp installieren auf LAB-08A')
        self.assertFalse(any(path=='/api/software/start' for path,_ in self.calls))
        self.page.locator('#catalog-confirm button[value="start"]').click()
        expect(self.page.locator('#software-status')).to_contain_text('Installation gestartet')
        run=self.page.locator('#software-runs details').first
        run.locator('summary').click();run.get_by_role('button',name='Protokoll',exact=True).click()
        expect(self.page.locator('#software-log')).to_contain_text('software installed')
        self.page.locator('#software-log-close').click();expect(self.page.locator('#software-log-panel')).to_be_hidden()
        run.get_by_role('button',name='Pilot abnehmen').click();self.page.locator('#catalog-confirm button[value="start"]').click()
        expect(self.page.locator('#software-profile-baseline')).to_be_enabled()
        self.assertFalse(self.errors,self.errors)

    def test_teacher_software_setup_hides_automatically_and_is_not_in_student_selection(self):
        self.page.goto(BASE+'/repo-software')
        expect(self.page.locator('[data-software-host="LAB-Teacher"]')).to_be_disabled()
        expect(self.page.locator('#software-teacher-setup')).to_be_visible()
        expect(self.page.locator('#software-teacher-setup a')).to_have_attribute('href','/cisco-lab/api/teacher/setup')
        self.page.locator('#software-teacher-check').click()
        expect(self.page.locator('#software-teacher-setup')).to_be_hidden()
        expect(self.page.locator('[data-software-host="LAB-Teacher"]')).to_be_enabled()
        self.page.locator('#software-target-all').click()
        expect(self.page.locator('[data-software-host="LAB-08A"]')).to_be_checked()
        expect(self.page.locator('[data-software-host="LAB-Teacher"]')).not_to_be_checked()
        self.page.locator('#software-target-none').click()
        self.page.locator('[data-software-host="LAB-Teacher"]').check()
        self.page.locator('#software-search').fill('gimp')
        self.page.locator('[data-software-add="gimp"]').check()
        self.page.locator('#software-plan').click()
        expect(self.page.locator('#software-install')).to_be_enabled()
        self.assertIn(('/api/software/plan',dict(packages=['gimp'],targets=['LAB-Teacher'],profile_id='')),self.calls)
        self.page.reload()
        expect(self.page.locator('#software-teacher-setup')).to_be_hidden()
        self.assertEqual(self.errors,[])

    def test_teacher_profile_poll_continues_if_running_snapshot_already_reports_free_lock(self):
        self.software['teacher']=dict(ready=True,error='')
        self.software['hosts'].append('LAB-Teacher')
        self.software['profiles']=[dict(id='a'*32,name='Tools',packages=['gimp'],baseline=False,approved=None)]
        self.page.goto(BASE+'/repo-software')
        self.page.clock.install()
        self.page.locator('#software-profile').select_option('a'*32)
        self.page.locator('[data-software-host="LAB-Teacher"]').check()
        def pending(route):
            body=route.request.post_data_json
            data=dict(id='b'*32,kind='plan',status='running',packages=body['packages'],targets=body['targets'],
                      profile_id=body['profile_id'],started='2026-11-09T14:26:57+01:00',results={},active=['LAB-Teacher'])
            self.software['runs'].insert(0,data);self.software['busy']=False
            route.fulfill(content_type='application/json',body=json.dumps(data))
        self.page.route('**/api/software/plan',pending)
        self.page.locator('#software-plan').click()
        expect(self.page.locator('#software-preview-status')).to_contain_text('werden geprüft')
        expect(self.page.locator('#software-install')).to_be_disabled()
        expect(self.page.locator('#software-install-hint')).to_contain_text('werden noch geprüft')
        run=self.software['runs'][0]
        run.update(status='succeeded',active=[],finished=self.page.evaluate('new Date().toISOString()'),results={
            'LAB-Teacher':dict(status='succeeded',host='LAB-Teacher',packages=['gimp'],
                changes=[dict(name='gimp',action='install',old=None,version='3.0')],already=[],removals=[],download_bytes=1000000)})
        self.software['busy']=False
        self.page.clock.fast_forward(2600)
        expect(self.page.locator('#software-preview-status')).to_contain_text('Prüfung abgeschlossen')
        expect(self.page.locator('#software-install')).to_be_enabled()
        self.assertEqual(self.errors,[])

    def test_software_current_plan_survives_browsing_older_history_and_selection_changes(self):
        self.page.goto(BASE+'/repo-software')
        self.page.locator('#software-search').fill('gimp')
        self.page.locator('[data-software-add="gimp"]').check()
        self.page.locator('[data-software-host="LAB-08A"]').check()
        self.page.locator('#software-plan').click()
        expect(self.page.locator('#software-install')).to_be_enabled()
        plan=self.software['runs'][0]
        self.software['runs'] += [dict(plan,id=format(i,'032x'),status='failed') for i in range(12)]
        self.page.locator('#software-refresh').click()
        self.page.locator('#software-runs-pagination button[data-direction="next"]').click()
        expect(self.page.locator('#software-runs-pagination')).to_contain_text('Seite 2')
        expect(self.page.locator('#software-install')).to_be_enabled()
        expect(self.page.locator('#software-preview-status')).to_contain_text('Prüfung abgeschlossen')
        self.assertIn(('/api/software/run/'+plan['id'],None),self.calls)
        self.page.locator('[data-software-host="LAB-08A"]').uncheck()
        expect(self.page.locator('#software-install')).to_be_disabled()
        self.page.locator('#software-refresh').click()
        expect(self.page.locator('#software-install')).to_be_disabled()
        self.assertEqual(self.errors,[])

    def test_software_profile_save_waits_for_new_snapshot_after_older_get(self):
        self.page.goto(BASE+'/repo-software')
        self.page.locator('#software-search').fill('gimp')
        self.page.locator('[data-software-add="gimp"]').check()
        self.page.locator('#software-profile-name').fill('Neue Tools')
        self.hold_software_snapshot_during_mutation()
        # Concurrent read requests share the existing GET, rather than adding work.
        self.page.evaluate('void loadSoftware(); void loadSoftware()')
        self.page.locator('#software-profile-save').click()
        self.page.wait_for_function('softwareProfileId === "'+'a'*32+'"')
        self.release_older_software_snapshot()
        expect(self.page.locator('#software-status')).to_have_text('Softwareprofil gespeichert.')
        expect(self.page.locator('#software-profile')).to_have_value('a'*32)
        expect(self.page.locator('#software-profile option:checked')).to_have_text('Neue Tools')
        self.assertEqual(self.software_snapshot_requests, 2)
        self.assertEqual(self.errors, [])

    def test_software_plan_after_older_get_keeps_installation_available(self):
        self.page.goto(BASE+'/repo-software')
        self.page.locator('#software-search').fill('gimp')
        self.page.locator('[data-software-add="gimp"]').check()
        self.page.locator('[data-software-host="LAB-08A"]').check()
        self.hold_software_snapshot_during_mutation()
        self.page.locator('#software-plan').click()
        self.page.wait_for_function('softwarePlanId === "'+'b'*32+'"')
        self.release_older_software_snapshot()
        expect(self.page.locator('#software-status')).to_contain_text('Prüfung gestartet.')
        expect(self.page.locator('#software-install')).to_be_enabled()
        expect(self.page.locator('#software-preview-status')).to_contain_text('Prüfung abgeschlossen.')
        self.assertEqual(self.software_snapshot_requests, 2)
        self.assertFalse(self.page.evaluate('backgroundPolls.get("software")?.pending || false'))
        self.assertEqual(self.errors, [])

    def test_software_history_page_change_survives_older_get(self):
        self.software['runs'] = [dict(id=format(number,'032x'),kind='plan',status='failed',
            packages=['gimp'],targets=['LAB-08A'],results={},started='2026-11-08T17:00:00+01:00')
            for number in range(16)]
        self.page.goto(BASE+'/repo-software')
        expect(self.page.locator('#software-runs-pagination')).to_contain_text('Seite 1 von 2')
        self.hold_software_snapshot_during_mutation()
        self.page.locator('#software-runs-pagination button[data-direction="next"]').click()
        self.release_older_software_snapshot()
        expect(self.page.locator('#software-runs-pagination')).to_contain_text('Seite 2 von 2')
        expect(self.page.locator('#software-runs > details')).to_have_count(6)
        self.assertEqual(self.page_calls[-1], ('/api/software', 2))
        self.assertEqual(self.errors, [])

    def test_stale_running_history_does_not_claim_a_free_operation_lock(self):
        self.software['busy']=False
        self.software['runs']=[dict(id='f'*32,kind='plan',status='running',packages=['old'],
            targets=['LAB-08A'],profile_id='',started='2026-11-09T10:00:00+01:00',results={},active=[])]
        self.page.goto(BASE+'/repo-software')
        self.page.locator('#software-search').fill('gimp')
        self.page.locator('[data-software-add="gimp"]').check()
        self.page.locator('[data-software-host="LAB-08A"]').check()
        expect(self.page.locator('#software-plan')).to_be_enabled()
        self.page.wait_for_timeout(2700)
        self.assertFalse(self.page.evaluate('backgroundPolls.get("software")?.pending || false'))
        self.assertEqual(self.errors,[])

    def hold_software_snapshot_during_mutation(self):
        from dashboard.pagination import paginate
        # Capture the pre-mutation response exactly as a slow server would.
        self.older_software_snapshot = json.dumps(paginate(self.software, 1))
        self.software_snapshot_requests = 0
        self.held_software_route = None
        def hold_first(route):
            self.software_snapshot_requests += 1
            if self.held_software_route is None:
                self.held_software_route = route
            else:
                route.fallback()
        self.page.route('**/api/software/page/1', hold_first)
        with self.page.expect_request('**/api/software/page/1'):
            self.page.evaluate('void loadSoftware()')

    def release_older_software_snapshot(self):
        self.held_software_route.fulfill(content_type='application/json', body=self.older_software_snapshot)

    def test_own_package_upload_select_profile_and_teacher_plan_use_the_shared_workflow(self):
        self.software['teacher']=dict(ready=True,error='');self.software['hosts'].append('LAB-Teacher')
        self.page.goto(BASE+'/repo-software')
        self.page.locator('#software-own-upload').set_input_files(dict(name='example.deb',mimeType='application/octet-stream',buffer=b'fixture-deb'))
        expect(self.page.locator('#software-own-results')).to_contain_text('lab-example')
        expect(self.page.locator('#software-own-results')).to_contain_text('1.0 · all')
        self.assertFalse(any(path=='/api/software/start' for path,_ in self.calls))
        self.page.locator('[data-software-add="lab-example"]').check()
        self.page.locator('#software-profile-name').fill('Eigene Tools')
        self.page.locator('#software-profile-save').click()
        expect(self.page.locator('#software-export')).to_contain_text('ZIP')
        self.page.locator('[data-software-host="LAB-Teacher"]').check()
        self.page.locator('#software-plan').click();expect(self.page.locator('#software-install')).to_be_enabled()
        self.assertIn(('/api/software/plan',dict(packages=['lab-example'],targets=['LAB-Teacher'],profile_id='a'*32)),self.calls)
        self.assertEqual(self.errors,[])

    def test_own_package_list_is_searchable_paged_and_readers_cannot_upload_or_select(self):
        self.software['own_packages']=[dict(name='lab-'+format(i,'03d'),version='1.0',architecture='all',
            description='LAB example',size=123,sha256='d'*64) for i in range(12)]
        self.page.goto(BASE+'/repo-software')
        expect(self.page.locator('#software-own-results article')).to_have_count(10)
        self.page.locator('#software-own-next').click();expect(self.page.locator('#software-own-results article')).to_have_count(2)
        self.page.locator('#software-own-search').fill('lab-001');expect(self.page.locator('#software-own-results article')).to_have_count(1)
        expect(self.page.locator('#software-own-pagination')).to_be_hidden()
        self.role='reader';self.page.reload()
        expect(self.page.locator('#software-own-upload')).to_be_disabled()
        expect(self.page.locator('#software-own-results input').first).to_be_disabled()
        self.assertFalse(any(payload is not None and path=='/api/software/package/upload' for path,payload in self.calls))

    def test_software_reader_and_mobile_keep_actions_disabled_and_layout_in_view(self):
        self.role='reader';self.page.set_viewport_size(dict(width=390,height=844))
        self.page.goto(BASE+'/repo-software')
        self.page.locator('#software-search').fill('gimp')
        expect(self.page.locator('#software-results')).to_contain_text('gimp')
        expect(self.page.locator('[data-software-add="gimp"]')).to_be_disabled()
        self.assertTrue(self.page.evaluate('document.documentElement.scrollWidth <= innerWidth'))
        self.assertFalse(any(payload is not None for _,payload in self.calls))
        self.assertFalse(self.errors,self.errors)

    def test_software_changing_targets_invalidates_installation_preview(self):
        self.page.goto(BASE+'/repo-software')
        self.page.locator('#software-search').fill('gimp')
        self.page.locator('[data-software-add="gimp"]').click()
        self.page.locator('[data-software-host="LAB-08A"]').check()
        self.page.locator('#software-plan').click()
        expect(self.page.locator('#software-install')).to_be_enabled()
        self.page.locator('[data-software-host="LAB-08A"]').uncheck()
        expect(self.page.locator('#software-install')).to_be_disabled()
        expect(self.page.locator('#software-preview')).to_be_hidden()
        self.assertFalse(self.errors,self.errors)

    def test_software_starts_empty_and_only_explicit_inventory_reads_clients(self):
        self.page.goto(BASE+'/repo-software')
        expect(self.page.locator('#software-search-status')).to_contain_text('mindestens 2 Zeichen')
        expect(self.page.locator('#software-search-pagination')).to_be_hidden()
        self.assertFalse(any(path.startswith('/api/software/search/') for path,_ in self.calls))
        self.page.locator('#software-search').fill('gimp')
        expect(self.page.locator('#software-results')).to_contain_text('Image editor')
        self.assertEqual(self.page.get_by_role('button',name='Hinzufügen',exact=True).count(),0)
        self.page.locator('[data-software-host="LAB-08A"]').check()
        expect(self.page.locator('[data-software-installed="gimp"]')).to_contain_text('Noch nicht geprüft')
        self.assertFalse(any(payload is not None for _,payload in self.calls))
        self.page.locator('#software-inventory').click()
        expect(self.page.locator('[data-software-installed="gimp"]')).to_contain_text('Installiert · 3.0')
        self.assertIn(('/api/software/inventory',dict(targets=['LAB-08A'])),self.calls)
        self.page.locator('[data-software-add="gimp"]').check()
        self.page.locator('#software-search').fill('')
        expect(self.page.locator('#software-results')).to_be_empty()
        expect(self.page.locator('#software-cart')).to_contain_text('gimp')
        self.assertEqual(sum(path=='/api/software/inventory' for path,_ in self.calls),1)
        self.assertFalse(self.errors,self.errors)

    def test_software_preview_names_dependencies_and_existing_versions(self):
        self.page.goto(BASE+'/repo-software')
        self.page.evaluate("softwareRenderPreview({kind:'plan',status:'succeeded',packages:['gimp','nmap'],targets:['LAB-08A'],results:{'LAB-08A':{changes:[{name:'gimp',action:'install',version:'3.0'},{name:'libexample',action:'install',version:'1.0'}],already:[{name:'nmap',version:'7.95'}],download_bytes:1024}}})")
        expect(self.page.locator('#software-preview')).to_contain_text('davon 1 Abhängigkeit')
        expect(self.page.locator('#software-preview')).to_contain_text('Abhängigkeit · Neu: libexample')
        expect(self.page.locator('#software-preview')).to_contain_text('Bereits installiert: nmap (7.95)')

    def test_software_already_installed_needs_no_new_installation(self):
        self.page.goto(BASE+'/repo-software')
        self.page.evaluate("softwareState.runs=[{id:'already',kind:'plan',status:'succeeded',packages:['gimp'],targets:['LAB-08A'],results:{'LAB-08A':{status:'succeeded',changes:[],already:[{name:'gimp',version:'3.0'}],download_bytes:0}}}];softwarePlanId='already';softwareCart=new Set(['gimp']);softwareHosts=new Set(['LAB-08A']);softwareRenderPreview(softwareState.runs[0]);softwareButtons()")
        expect(self.page.locator('#software-preview-status')).to_contain_text('Keine Installation nötig')
        expect(self.page.locator('#software-install')).to_be_disabled()

    def test_fresh_b_whole_gui_path_without_manual_hostkey_or_ssh_help(self):
        self.page.goto(BASE+'/installationsfolge')
        expect(self.page.locator('#intake-gate')).to_be_visible()
        expect(self.page.locator('[data-intake-host="LAB-08B"]')).to_be_checked()
        expect(self.page.locator('.fresh-target-empty')).to_be_visible()
        expect(self.page.locator('#fresh-empty')).to_be_visible()
        self.assertTrue(self.page.evaluate('document.documentElement.scrollWidth <= innerWidth'))
        self.assertFalse(any(payload is not None for _, payload in self.calls))
        expect(self.page.locator('#intake-student')).to_be_hidden()
        self.page.locator('#intake-password').fill('test-ephemeral')
        self.page.locator('#intake-read').click()
        self.assertFalse(any(path == '/api/intake/read' for path, _ in self.calls))
        self.page.locator('#intake-image').check()
        self.page.locator('#intake-read').click()
        expect(self.page.locator('#intake-review')).to_be_visible()
        self.assertEqual(next(payload for path,payload in self.calls if path=='/api/intake/read')['student'],'')
        self.page.locator('#intake-setup').click()
        self.assertFalse(any(path=='/api/intake/setup' for path,_ in self.calls))
        self.page.locator('#intake-student').fill('student')
        self.page.set_viewport_size({'width': 390, 'height': 844})
        self.assertTrue(self.page.evaluate('document.documentElement.scrollWidth <= innerWidth'))
        self.page.locator('#intake-setup').click()
        expect(self.page.locator('#intake-password')).to_have_value('')
        expect(self.page.locator('#fresh-targets input[data-host="LAB-08B"]')).to_be_visible()
        self.page.locator('#fresh-targets input[data-host="LAB-08B"]').check()
        self.page.locator('#fresh-start').click()
        self.page.locator('#catalog-confirm button[value="start"]').click()
        expect(self.page.locator('#intake-accept-panel')).to_be_visible()
        expect(self.page.locator('#fresh-runs .setup-run h3')).to_have_text('LAB-08B')
        expect(self.page.locator('#fresh-runs .badge')).to_have_text('Erfolgreich')
        expect(self.page.locator('#fresh-empty')).to_be_hidden()
        self.assertTrue(self.page.evaluate('document.documentElement.scrollWidth <= innerWidth'))
        self.page.locator('#intake-tested').check()
        self.page.locator('#intake-accept').click()
        expect(self.page.locator('#intake-gate')).to_be_hidden()
        self.assertTrue(self.accepted)
        self.page.reload()
        expect(self.page.locator('#intake-gate')).to_be_hidden()
        self.assertFalse(self.errors, self.errors)

    def test_bulk_intake_shares_credentials_and_keeps_per_host_adapters(self):
        self.accepted = True
        self.page.goto(BASE+'/installationsfolge')
        self.page.locator('#intake-all').click()
        expect(self.page.locator('[data-intake-host]:checked')).to_have_count(2)
        self.page.locator('#intake-password').fill('test-ephemeral')
        self.page.locator('#intake-image').check()
        self.page.locator('#intake-read').click()
        expect(self.page.locator('[data-intake-adapter]')).to_have_count(2)
        expect(self.page.locator('#intake-summary')).to_contain_text('2 Geräte geprüft')
        expect(self.page.locator('#fresh-parallelism')).to_contain_text('4 gleichzeitig')
        self.page.locator('#intake-student').fill('student')
        self.page.set_viewport_size({'width':390,'height':844})
        self.assertTrue(self.page.evaluate('document.documentElement.scrollWidth <= innerWidth'))
        self.page.locator('#intake-setup').click()
        expect(self.page.locator('#intake-password')).to_have_value('')
        expect(self.page.locator('#intake-sudo')).to_have_value('')
        expect(self.page.locator('#fresh-targets input[data-host]')).to_have_count(2)
        read = next(payload for path,payload in self.calls if path=='/api/intake/read-many')
        setup = next(payload for path,payload in self.calls if path=='/api/intake/setup-many')
        self.assertCountEqual(read['hosts'], ['LAB-01A','LAB-08B'])
        self.assertEqual(len(setup['targets']),2)
        self.assertTrue(all(row['lab_interface']=='enx001122334466' for row in setup['targets']))
        self.assertFalse(any(path=='/api/fresh/start' for path,_ in self.calls))
        self.assertFalse(self.errors,self.errors)

    def test_failed_device_read_can_be_retried_with_reentered_password(self):
        self.read_failure = 'SSH-Anmeldung abgewiesen.'
        self.page.goto(BASE+'/installationsfolge')
        self.page.locator('#intake-password').fill('wrong')
        self.page.locator('#intake-image').check()
        self.page.locator('#intake-read').click()
        expect(self.page.locator('#intake-password')).to_have_value('')
        self.read_failure = None
        self.page.locator('#intake-password').fill('test-ephemeral')
        self.page.evaluate('loadIntake()')
        expect(self.page.locator('#intake-password')).to_have_value('test-ephemeral')
        self.page.locator('#intake-read').click()
        expect(self.page.locator('#intake-review')).to_be_visible()
        reads = [payload for path,payload in self.calls if path=='/api/intake/read']
        self.assertEqual(reads[-1]['ssh_password'],'test-ephemeral')
        self.assertFalse(self.errors,self.errors)

    def test_bulk_intake_shows_partial_failure_without_blocking_healthy_client(self):
        self.accepted = True; self.read_failure='Kein USB-Adapter gefunden.'
        self.page.goto(BASE+'/installationsfolge')
        self.page.locator('#intake-all').click()
        self.page.locator('#intake-password').fill('test-ephemeral')
        self.page.locator('#intake-image').check()
        self.page.locator('#intake-read').click()
        expect(self.page.locator('.intake-result[data-state="failed"]')).to_contain_text(self.read_failure)
        expect(self.page.locator('#intake-review')).to_be_visible()
        expect(self.page.locator('[data-intake-adapter]')).to_have_count(1)
        expect(self.page.locator('#intake-password')).to_have_value('test-ephemeral')
        self.assertFalse(self.errors,self.errors)

    def test_teacher_combined_wake_and_unsupported_actions(self):
        self.page.goto(BASE+'/sammelaktionen')
        expect(self.page.locator('#room-prepare')).to_be_enabled()
        self.assertEqual(self.page.locator('#room-targets input[data-room-host]:checked').count(), 16)
        expect(self.page.locator('input[data-room-host="LAB-Teacher"]')).not_to_be_checked()
        self.assertFalse(self.page.locator('#room-action option[value="poweroff"]').is_disabled())
        self.page.locator('#room-with-teacher').click()
        self.assertEqual(self.page.locator('#room-targets input[data-room-host]:checked').count(), 17)
        self.assertFalse(self.page.locator('#room-action option[value="poweroff"]').is_disabled())
        self.page.locator('#room-prepare').click()
        expect(self.page.locator('#room-confirm-hosts')).to_contain_text('LAB-Teacher')
        with self.page.expect_response('**/api/room/start'):
            self.page.locator('#room-confirm button[value="start"]').click()
        self.page.wait_for_function('!document.getElementById("room-prepare").disabled')
        from dashboard.room import TARGETS
        self.assertIn(('/api/room/start', dict(action='wake', targets=[*TARGETS,'LAB-Teacher'], confirmed=True)), self.calls)
        self.page.locator('#room-all-students').click()
        self.assertFalse(self.page.locator('#room-action option[value="poweroff"]').is_disabled())
        self.page.locator('#room-clear').click()
        self.page.locator('input[data-room-host="LAB-Teacher"]').check()
        self.assertTrue(self.page.locator('#room-action option[value="reboot"]').is_disabled())
        self.assertFalse(self.page.locator('#room-action option[value="poweroff"]').is_disabled())
        self.assertFalse(self.errors, self.errors)

    def test_room_checkbox_selection_preserves_exact_mixed_targets_and_team_state(self):
        self.page.goto(BASE+'/sammelaktionen')
        self.page.locator('#room-clear').click()
        expect(self.page.locator('#room-prepare')).to_be_disabled()
        for host in ('LAB-03A', 'LAB-04B'):
            self.page.locator('input[data-room-host="'+host+'"]').check()
        self.page.locator('button[data-room-team="05"]').click()
        expect(self.page.locator('button[data-room-team="03"]')).to_have_attribute('aria-pressed', 'mixed')
        self.assertEqual(self.page.locator('#room-targets input').count(),17)
        expect(self.page.locator('[data-presence-host="LAB-08A"]')).to_have_attribute('data-state','online')
        expect(self.page.locator('[data-presence-host="LAB-08B"]')).to_have_attribute('data-state','unreachable')
        cards = self.page.locator('.room-selection').bounding_box(), self.page.locator('.room-command').bounding_box()
        self.assertAlmostEqual(cards[0]['y'],cards[1]['y'],delta=1)
        self.assertAlmostEqual(cards[0]['height'],cards[1]['height'],delta=1)
        expected = ['LAB-03A','LAB-04B','LAB-05A','LAB-05B']
        self.page.locator('#room-action').select_option('poweroff')
        self.page.evaluate('loadRoom()')
        self.assertEqual(self.page.locator('input[data-room-host]:checked').evaluate_all('inputs => inputs.map(input=>input.value).sort()'), expected)
        expect(self.page.locator('#room-selection-count')).to_have_text('4 Geräte ausgewählt')
        self.page.set_viewport_size({'width':390,'height':844})
        self.assertTrue(self.page.evaluate('document.documentElement.scrollWidth <= innerWidth'))
        self.page.locator('#room-prepare').click()
        expect(self.page.locator('#room-confirm-hosts .badge')).to_have_count(4)
        self.assertEqual(self.page.locator('#room-confirm-hosts .badge').all_text_contents(), expected)
        self.assertFalse(any(path == '/api/room/start' for path,_ in self.calls))
        self.page.locator('#room-confirm button[value="cancel"]').click()
        self.assertFalse(any(path == '/api/room/start' for path,_ in self.calls))
        self.page.locator('#room-prepare').click()
        with self.page.expect_response('**/api/room/start'):
            self.page.locator('#room-confirm button[value="start"]').click()
        expect(self.page.locator('#room-runs > details')).to_have_count(1)
        self.page.locator('#room-runs summary').click()
        expect(self.page.locator('#room-runs li')).to_have_count(4)
        self.assertIn(('/api/room/start', dict(action='poweroff', targets=expected, confirmed=True)), self.calls)
        self.assertFalse(self.errors, self.errors)

    def test_room_selection_waits_for_metadata_and_stays_disabled_on_read_error(self):
        held = []
        self.context.route('**/api/room/page/*', lambda route: held.append(route))
        self.page.goto(BASE+'/sammelaktionen', wait_until='domcontentloaded')
        expect(self.page.locator('#room-all-students')).to_be_disabled()
        expect(self.page.locator('#room-action')).to_be_disabled()
        self.page.wait_for_function('document.readyState === "complete"')
        self.assertEqual(len(held), 1)
        held[0].fulfill(status=503, content_type='application/json', body=json.dumps(dict(error='Fixture: Zustand nicht verfügbar')))
        expect(self.page.locator('#room-status')).to_have_text('Fixture: Zustand nicht verfügbar')
        expect(self.page.locator('#room-all-students')).to_be_disabled()
        expect(self.page.locator('#room-prepare')).to_be_disabled()
        self.context.unroute('**/api/room/page/*')
        self.page.evaluate('loadRoom()')
        expect(self.page.locator('#room-all-students')).to_be_enabled()
        expect(self.page.locator('#room-prepare')).to_be_enabled()
        self.assertFalse(any(path == '/api/room/start' for path, _ in self.calls))
        self.assertFalse(self.errors, self.errors)

    def test_room_team_background_and_keyboard_toggle_without_changing_single_clicks(self):
        self.page.goto(BASE+'/sammelaktionen')
        self.page.locator('#room-clear').click()
        group=self.page.locator('.room-team').filter(has=self.page.locator('[data-room-team="05"]'))
        group.click(position={'x':3,'y':3})
        self.assertEqual(group.locator('input:checked').count(),2)
        group.locator('input[value="LAB-05A"]').uncheck()
        self.assertEqual(group.locator('input:checked').count(),1)
        header=group.locator('button[data-room-team]')
        expect(header).to_have_attribute('aria-pressed','mixed')
        header.focus();self.page.keyboard.press('Space')
        self.assertEqual(group.locator('input:checked').count(),2)
        self.page.keyboard.press('Enter')
        self.assertEqual(group.locator('input:checked').count(),0)
        self.assertFalse(any(path=='/api/room/start' for path,_ in self.calls))
        self.assertFalse(self.errors,self.errors)

    def test_room_unenrolled_clients_are_wake_only_and_old_partial_failure_is_visible(self):
        from dashboard.room import TARGETS
        self.room_access={host:host=='LAB-08A' for host in TARGETS}
        self.histories['/api/room']=[dict(id='a'*32,action='poweroff',status='finished',targets=list(TARGETS),started='2026-11-08T16:12:00+01:00',
            results={host:'Systemaktion eingeplant.' if host=='LAB-08A' else 'Fehler: Host key verification failed.' for host in TARGETS})]
        self.page.goto(BASE+'/sammelaktionen')
        expect(self.page.locator('#room-capability')).to_contain_text('15 Clients sind noch nicht auf APP aufgenommen')
        self.assertTrue(self.page.locator('#room-action option[value="poweroff"]').is_disabled())
        expect(self.page.locator('#room-runs summary')).to_contain_text('Teilweise fehlgeschlagen')
        expect(self.page.locator('#room-runs summary')).to_contain_text('15 Fehler')
        self.page.locator('#room-ready').click()
        self.assertEqual(self.page.locator('input[data-room-host]:checked').evaluate_all('nodes=>nodes.map(node=>node.value)'),['LAB-08A'])
        self.assertFalse(self.page.locator('#room-action option[value="poweroff"]').is_disabled())
        self.page.locator('#room-action').select_option('poweroff')
        self.page.locator('#room-prepare').click()
        expect(self.page.locator('#room-confirm-hosts .badge')).to_have_text('LAB-08A')
        self.page.locator('#room-confirm button[value="cancel"]').click()
        self.page.set_viewport_size({'width':390,'height':844})
        self.assertTrue(self.page.evaluate('document.documentElement.scrollWidth <= innerWidth'))
        self.assertFalse(any(payload is not None for _,payload in self.calls))
        self.assertFalse(self.errors,self.errors)

    def test_fresh_log_closes_and_stays_closed_after_poll_and_late_reply(self):
        self.runs = [dict(id='c'*32, host='LAB-08B', status='succeeded', phase='maintenance',
                          started='2026-11-09T10:00:00+01:00')]
        self.page.goto(BASE+'/installationsfolge')
        log = self.page.locator('#fresh-runs').get_by_role('button', name='Protokoll', exact=True)
        log.click()
        expect(self.page.locator('#fresh-output')).to_have_text('Fixture: baseline complete')
        expect(log).to_have_attribute('aria-expanded', 'true')
        self.page.locator('#fresh-log-close').click()
        expect(self.page.locator('#fresh-log-panel')).to_be_hidden()
        probes = sum(path.startswith('/api/fresh/log/') for path, _ in self.calls)
        self.page.evaluate('loadFresh()')
        expect(self.page.locator('#fresh-log-panel')).to_be_hidden()
        expect(log).to_have_attribute('aria-expanded', 'false')
        self.assertEqual(sum(path.startswith('/api/fresh/log/') for path, _ in self.calls), probes)
        self.page.evaluate('''() => {
          const originalRequest = request, originalShow = showFreshLog;
          request = async (...args) => {
            const data = await originalRequest(...args);
            if (args[0].startsWith('/api/fresh/log/')) await new Promise(resolve => window.releaseLog = resolve);
            return data;
          };
          showFreshLog = run => window.pendingLog = originalShow(run);
        }''')
        log.click()
        self.page.wait_for_function('typeof window.releaseLog === "function"')
        self.page.locator('#fresh-log-close').click()
        self.page.evaluate('async () => { window.releaseLog(); await window.pendingLog; }')
        expect(self.page.locator('#fresh-log-panel')).to_be_hidden()
        expect(self.page.locator('#fresh-output')).to_have_text('')
        self.assertFalse(self.errors, self.errors)

    def test_maintenance_reads_live_only_on_click_and_requires_confirmation(self):
        self.maintenance_hosts = ['LAB-08A', 'LAB-01A']
        self.maintenance_can_start = True
        self.page.goto(BASE+'/wartung')
        expect(self.page.locator('#maintenance-count')).to_have_text('1')
        expect(self.page.locator('#maintenance-teacher')).to_have_text('Freigegeben')
        expect(self.page.locator('#maintenance-refresh')).to_be_disabled()
        expect(self.page.locator('#maintenance-start')).to_be_disabled()
        self.assertFalse(any(path.startswith('/api/maintenance/') for path, _ in self.calls))
        self.page.locator('#maintenance-targets input[value="LAB-08A"]').check()
        expect(self.page.locator('#maintenance-start')).to_be_enabled()
        self.page.locator('#maintenance-refresh').click()
        expect(self.page.locator('#maintenance-start')).to_be_enabled()
        expect(self.page.locator('#maintenance-output')).to_contain_text('Fixture')
        self.page.locator('#maintenance-details summary').click()
        expect(self.page.locator('#maintenance-output')).to_be_visible()
        self.page.locator('#maintenance-details summary').click()
        expect(self.page.locator('#maintenance-output')).to_be_hidden()
        self.page.locator('#maintenance-start').click()
        expect(self.page.locator('#catalog-confirm')).to_be_visible()
        self.assertFalse(any(path == '/api/maintenance/start' for path, _ in self.calls))
        self.page.locator('#catalog-confirm button[value="cancel"]').click()
        self.page.locator('#maintenance-none').click()
        expect(self.page.locator('#maintenance-start')).to_be_disabled()
        self.page.locator('#maintenance-targets input[value="LAB-01A"]').check()
        expect(self.page.locator('#maintenance-start')).to_be_enabled()
        self.assertEqual(sum(path.startswith('/api/maintenance/') for path, _ in self.calls), 1)
        self.page.set_viewport_size({'width':390,'height':844})
        self.assertTrue(self.page.evaluate('document.documentElement.scrollWidth <= innerWidth'))
        self.page.locator('#maintenance-refresh').click()
        expect(self.page.locator('#maintenance-start')).to_be_enabled()
        self.page.locator('#maintenance-start').click()
        with self.page.expect_response('**/api/maintenance/start'):
            self.page.locator('#catalog-confirm button[value="start"]').click()
        expect(self.page.locator('#maintenance-status')).to_contain_text('LAB-01A')
        self.assertIn(('/api/maintenance/start', dict(hosts=['LAB-01A'], confirmed=True)), self.calls)
        self.assertFalse(self.errors, self.errors)

    def test_manual_maintenance_all_or_selected_needs_no_live_probe(self):
        self.maintenance_hosts = [f'LAB-{number:02d}{side}' for number in range(1,9) for side in 'AB']
        self.page.goto(BASE+'/wartung')
        self.page.locator('#maintenance-all').click()
        expect(self.page.locator('#maintenance-targets input:checked')).to_have_count(16)
        self.page.locator('#maintenance-targets input[value="LAB-04B"]').uncheck()
        self.page.locator('#maintenance-start').click()
        expect(self.page.locator('#catalog-confirm-text')).not_to_contain_text('LAB-04B')
        self.assertFalse(any(path.startswith('/api/maintenance/') for path, _ in self.calls))
        self.page.locator('#catalog-confirm button[value="start"]').click()
        expect(self.page.locator('#maintenance-status')).to_contain_text('Wartung gestartet')
        started = [payload for path,payload in self.calls if path == '/api/maintenance/start']
        self.assertEqual(started[0]['hosts'], [host for host in self.maintenance_hosts if host != 'LAB-04B'])
        self.assertFalse(self.errors, self.errors)

    def test_maintenance_current_run_shows_pending_and_finished_clients_across_history_pages(self):
        self.maintenance_hosts = ['LAB-08A', 'LAB-08B']
        self.maintenance_busy = True
        running = dict(id='a'*32,status='running',started='2026-11-09T13:58:43+01:00',manual_start=True,
                       targets=self.maintenance_hosts,results={'LAB-08B':dict(status='succeeded')})
        self.histories['/api/maintenance-runs'] = [running] + [dict(id=f'{i:032x}',status='finished',
            started='2026-11-08T12:00:00+01:00',results={}) for i in range(20)]
        self.page.goto(BASE+'/wartung')
        card = self.page.locator('#maintenance-current')
        expect(card).to_be_visible(); expect(card).to_contain_text('1 von 2 Ergebnissen')
        expect(card).to_contain_text('LAB-08A');expect(card).to_contain_text('Endergebnis steht noch aus')
        expect(card).to_contain_text('LAB-08B');expect(card).to_contain_text('Neustart bestätigt')
        expect(self.page.locator('#maintenance-runs details').first).to_have_attribute('open','')
        self.page.locator('#maintenance-runs-pagination').get_by_role('button',name='Weiter',exact=True).click()
        expect(self.page.locator('#maintenance-runs-pagination')).to_contain_text('Seite 2 von 3')
        expect(card).to_contain_text('LAB-08A');expect(card).to_contain_text('LAB-08B')
        self.assertFalse(any(path.startswith('/api/maintenance/') for path,_ in self.calls))
        self.assertFalse(any(payload is not None for _,payload in self.calls))
        self.assertFalse(self.errors,self.errors)

    def test_finished_maintenance_only_remains_in_history(self):
        self.histories['/api/maintenance-runs'] = [dict(id='a'*32,status='finished',started='2026-11-09T13:58:43+01:00',
            manual_start=True,targets=['LAB-08A'],results={'LAB-08A':dict(status='succeeded')})]
        self.page.goto(BASE+'/wartung')
        expect(self.page.locator('#maintenance-current')).to_be_hidden()
        expect(self.page.locator('#maintenance-runs')).to_contain_text('Manuell')
        self.page.locator('#maintenance-runs summary').click()
        expect(self.page.locator('#maintenance-runs')).to_contain_text('Neustart bestätigt')
        self.assertFalse(self.errors,self.errors)

    def test_maintenance_package_evidence_is_saved_expandable_and_missing_is_unknown(self):
        self.histories['/api/maintenance-runs']=[dict(id='a'*32,status='finished',started='2026-11-13T18:00:00+01:00',
            targets=['LAB-01A','LAB-01B','LAB-02A'],results={
                'LAB-01A':dict(status='succeeded',packages=dict(status='recorded',upgraded=[dict(name='curl:amd64',versions='1.0, 1.1')],installed=[dict(name='helper:amd64',versions='2.0')],removed=[])),
                'LAB-01B':dict(status='succeeded',packages=dict(status='recorded',upgraded=[],installed=[],removed=[])),
                'LAB-02A':dict(status='succeeded')})]
        self.page.goto(BASE+'/wartung')
        run=self.page.locator('#maintenance-runs > details').first
        run.locator(':scope > summary').click()
        expect(run).to_contain_text('1 Update installiert')
        expect(run).to_contain_text('1 Paket neu installiert')
        expect(run).to_contain_text('Keine Updates installiert')
        expect(run).to_contain_text('Update-Ergebnis nicht erfasst')
        run.locator('.maintenance-packages summary').click()
        expect(run.locator('.maintenance-packages dd').first).to_have_text('curl:amd64 (1.0, 1.1)')
        run.locator('.maintenance-packages summary').click()
        expect(run.locator('.maintenance-packages dd').first).to_be_hidden()
        self.assertFalse(any(path.startswith('/api/maintenance/') for path,_ in self.calls))
        self.assertFalse(any(payload is not None for _,payload in self.calls))
        self.assertFalse(self.errors,self.errors)

    def test_client_update_and_cleanup_packages_expand_from_saved_data(self):
        client = next(row for row in self.client_report['clients'] if row['host']=='LAB-08A')
        client.update(update_packages=['curl','vim'],removals=1,removal_packages=['old-package'],apt_mode='central')
        self.page.goto(BASE+'/schuelerclients')
        self.page.locator('#clients-search').fill('08A')
        self.page.locator('summary[aria-label="2 Updates für LAB-08A"]').click()
        expect(self.page.locator('#client-rows')).to_contain_text('curl')
        expect(self.page.locator('#client-rows')).to_contain_text('vim')
        self.page.locator('summary[aria-label="1 Kandidat für LAB-08A"]').click()
        expect(self.page.locator('#client-rows')).to_contain_text('old-package')
        expect(self.page.locator('#client-rows')).to_contain_text('APP-gesteuert')
        self.assertFalse(any(payload is not None for _,payload in self.calls))
        self.assertFalse(self.errors,self.errors)

    def test_client_without_package_data_shows_unknown_instead_of_zero_updates(self):
        client=next(row for row in self.client_report['clients'] if row['host']=='LAB-08A')
        client.update(action='review',updates=0,updates_known=False,removals_known=False,reason='Client nicht erreichbar.')
        self.page.goto(BASE+'/schuelerclients')
        self.page.locator('#clients-search').fill('08A')
        cell=self.page.locator('#client-rows td').nth(1)
        expect(cell).to_have_text('—')
        expect(cell).to_have_attribute('title','Client nicht erreichbar.')
        expect(cell).to_have_attribute('aria-label','Updates: Paketstand nicht bekannt. Client nicht erreichbar.')
        self.assertFalse(self.errors,self.errors)

    def test_client_report_updates_visible_page_and_pauses_when_hidden(self):
        self.page.clock.install()
        client=next(row for row in self.client_report['clients'] if row['host']=='LAB-08A')
        self.page.goto(BASE+'/schuelerclients')
        self.page.locator('#clients-search').fill('08A')
        expect(self.page.locator('#client-rows')).to_contain_text('2')
        self.page.wait_for_function("() => backgroundPolls.get('clients')?.pending")
        client.update(updates=0,update_packages=[])
        self.page.clock.fast_forward(31000)
        expect(self.page.locator('#client-rows td').nth(1)).to_have_text('0')
        self.page.wait_for_function("() => backgroundPolls.get('clients')?.pending")
        count=sum(path=='/api/state' for path,_ in self.calls)
        self.page.evaluate("Object.defineProperty(document,'hidden',{configurable:true,get:()=>true}); document.dispatchEvent(new Event('visibilitychange'))")
        self.page.clock.fast_forward(65000)
        self.assertEqual(sum(path=='/api/state' for path,_ in self.calls),count)
        self.assertFalse(any(path=='/api/refresh' for path,_ in self.calls))
        self.assertFalse(self.errors,self.errors)

    def test_read_failure_shows_next_step_and_clears_password(self):
        self.read_failure = 'Kein USB-LAB-Adapter gefunden. Adapter anschließen und „Gerät prüfen“ erneut wählen.'
        self.page.goto(BASE+'/installationsfolge')
        expect(self.page.locator('[data-intake-host="LAB-08B"]')).to_be_checked()
        self.page.locator('#intake-password').fill('test-ephemeral')
        self.page.locator('#intake-image').check()
        self.page.locator('#intake-read').click()
        expect(self.page.locator('#intake-status')).to_have_text(self.read_failure)
        expect(self.page.locator('#intake-password')).to_have_value('')
        expect(self.page.locator('#intake-review')).to_be_hidden()
        self.assertFalse(any(path == '/api/intake/setup' for path, _ in self.calls))
        self.page.reload()
        expect(self.page.locator('#intake-status')).to_have_text(self.read_failure)
        self.assertFalse(self.errors, self.errors)

    def test_overview_mobile_has_no_overflow_and_shared_presence(self):
        self.page.set_viewport_size({'width': 390, 'height': 844})
        self.page.goto(BASE+'/')
        expect(self.page.locator('#overview-teacher')).to_have_text('Aktiv')
        self.assertEqual(self.page.locator('#overview-devices .device-card').count(), 4)
        self.assertTrue(self.page.evaluate('document.documentElement.scrollWidth <= window.innerWidth'))
        self.assertEqual({path for path, _ in self.calls}, {'/api/intake', '/api/teacher/status', '/api/presence', '/api/activity'})
        expect(self.page.locator('[data-presence-host="LAB-08A"]')).to_have_attribute('data-state', 'online')
        expect(self.page.locator('[data-presence-host="LAB-08B"]')).to_have_attribute('data-state', 'unreachable')
        expect(self.page.locator('#lab-navigation')).to_be_hidden()
        self.page.get_by_role('button', name='Menü', exact=True).click()
        expect(self.page.locator('#lab-navigation')).to_be_visible()
        expect(self.page.locator('#nav-toggle')).to_have_attribute('aria-expanded', 'true')
        self.page.keyboard.press('Escape')
        expect(self.page.locator('#lab-navigation')).to_be_hidden()
        expect(self.page.locator('#nav-toggle')).to_be_focused()
        self.assertFalse(self.errors, self.errors)

    def test_overview_links_preselect_device_without_starting_action(self):
        self.page.goto(BASE+'/')
        self.assertEqual(self.page.locator('.overview-metrics a').count(),4)
        self.assertEqual(self.page.locator('nav details').count(),0)
        self.page.locator('a.device-card[href$="#LAB-08A"]').click()
        expect(self.page.locator('input[data-room-host="LAB-08A"]')).to_be_checked()
        self.assertEqual(self.page.locator('input[data-room-host]:checked').count(), 1)
        self.assertFalse(any(path == '/api/room/start' for path,_ in self.calls))
        self.page.goto(BASE+'/installationsfolge#LAB-01A')
        expect(self.page.locator('[data-intake-host="LAB-01A"]')).to_be_checked()
        self.assertFalse(any(path == '/api/intake/read' for path,_ in self.calls))

    def test_login_audit_shows_names_and_rejection_reason(self):
        self.page.goto(BASE+'/anmeldungen')
        expect(self.page.locator('#login-log-rows')).to_contain_text('Testschueler')
        expect(self.page.locator('#login-log-rows')).to_contain_text('student.test')
        expect(self.page.locator('#login-log-rows')).to_contain_text('Nicht Mitglied des Kollegiums')
        self.assertFalse(self.errors,self.errors)

    def test_all_fresh_active_jobs_stay_visible_and_only_completed_history_is_paged(self):
        active=[dict(id=format(index+100,'032x'),host=f'LAB-{index//2+1:02d}{"AB"[index%2]}',
            started='2026-11-09T12:00:00+01:00',status='queued' if index<7 else 'running',phase='basis') for index in range(11)]
        completed=[dict(id=format(index+200,'032x'),host='LAB-08B',started='2026-11-08T12:00:00+01:00',
            status='succeeded',phase='maintenance') for index in range(35)]
        self.histories['/api/fresh']=active+completed
        self.page.goto(BASE+'/installationsfolge')
        panel=self.page.locator('#fresh-active-panel')
        expect(panel).to_be_visible()
        expect(self.page.locator('#fresh-active-runs > li')).to_have_count(11)
        self.assertEqual(sum(path=='/api/fresh' for path,_ in self.calls),
                         sum(path=='/api/fresh' for path,_ in self.page_calls))
        expect(self.page.locator('#fresh-active-runs > li[data-state="running"]')).to_have_count(4)
        expect(self.page.locator('#fresh-active-runs > li[data-state="queued"]')).to_have_count(7)
        states=self.page.locator('#fresh-active-runs > li').evaluate_all('nodes=>nodes.map(node=>node.dataset.state)')
        self.assertEqual(states,['running']*4+['queued']*7)
        self.assertLess(panel.bounding_box()['y'],self.page.locator('.setup-grid').bounding_box()['y'])
        pager=self.page.locator('#fresh-runs-pagination')
        expect(pager).to_contain_text('Seite 1 von 4')
        self.assertEqual(self.page.locator('#fresh-runs > li').first.get_attribute('data-history-key'),completed[0]['id'])
        for number in (2,3,4):
            pager.get_by_role('button',name='Weiter',exact=True).click()
            expect(pager).to_contain_text(f'Seite {number} von 4')
            expect(self.page.locator('#fresh-active-runs > li[data-state="running"]')).to_have_count(4)
            self.assertEqual(self.page.locator('#fresh-runs > li').first.get_attribute('data-history-key'),completed[(number-1)*10]['id'])
        expect(self.page.locator('#fresh-runs > li')).to_have_count(5)
        self.page.evaluate('loadFresh()')
        expect(pager).to_contain_text('Seite 4 von 4')
        expect(self.page.locator('#fresh-active-runs > li')).to_have_count(11)
        # A completed job moves to history while an open log still receives its
        # final update, even when its history row is on another page.
        logged=active[-1]
        self.page.locator('#fresh-active-runs .setup-run').filter(has=self.page.get_by_role('heading',name=logged['host'],exact=True)).get_by_role('button',name='Protokoll',exact=True).click()
        expect(self.page.locator('#fresh-log-panel')).to_be_visible()
        log_path='/api/fresh/log/'+logged['id']
        probes=sum(path==log_path for path,_ in self.calls)
        self.histories['/api/fresh']=active[:-1]+[dict(logged,status='succeeded',phase='maintenance')]+completed
        self.page.evaluate('loadFresh()')
        expect(self.page.locator('#fresh-active-runs > li[data-state="running"]')).to_have_count(3)
        expect(self.page.locator('#fresh-runs > li')).to_have_count(6)
        expect(pager).to_contain_text('36 Einträgen')
        self.assertGreater(sum(path==log_path for path,_ in self.calls),probes)
        self.page.set_viewport_size(dict(width=390,height=844))
        self.assertTrue(self.page.evaluate('document.documentElement.scrollWidth <= innerWidth'))
        self.assertFalse(any(payload is not None for _,payload in self.calls))
        self.assertFalse(self.errors,self.errors)

    def test_fresh_active_only_queue_has_no_history_pagination_and_readers_cannot_stop_it(self):
        self.role='reader'
        self.histories['/api/fresh']=[dict(id=format(index+100,'032x'),host='LAB-01A',
            started='2026-11-09T12:00:00+01:00',status='running' if index<4 else 'queued',phase='basis') for index in range(11)]
        self.page.goto(BASE+'/installationsfolge')
        expect(self.page.locator('#fresh-active-runs > li')).to_have_count(11)
        expect(self.page.locator('#fresh-runs > li')).to_have_count(0)
        expect(self.page.locator('#fresh-runs-pagination')).to_be_hidden()
        expect(self.page.locator('#fresh-empty')).to_be_visible()
        for button in self.page.locator('#fresh-active-runs').get_by_role('button',name='Anhalten',exact=True).all():
            expect(button).to_be_disabled()
        expect(self.page.locator('#fresh-active-runs').get_by_role('button',name='Protokoll',exact=True).first).to_be_enabled()
        self.assertFalse(any(payload is not None for _,payload in self.calls))
        self.assertFalse(self.errors,self.errors)

    def test_all_growing_histories_have_working_page_navigation(self):
        runs=[dict(id=f'{index:032x}',host='LAB-08B',started='2026-11-08T12:00:00+00:00',status='finished',
                   phase='basis',job='firefox',revision='a'*64,results={},targets=['LAB-08A'],pending=[],
                   scope='room',action='wake',file='fixture.tar.gz.gpg',pruned_at='2026-11-08') for index in range(35)]
        views=[('/installationsfolge','fresh-runs','/api/fresh'),('/pilotdeploys','pilot-runs','/api/pilot'),
               ('/sammelaktionen','room-runs','/api/room'),('/backup','backup-runs','/api/backup'),
               ('/desktopbilder','appearance-release-runs','/api/appearance/rollout'),
               ('/playbooks','catalog-runs','/api/rollout/state/firefox'),
               ('/wartung','maintenance-runs','/api/maintenance-runs')]
        for _,_,endpoint in views:self.histories[endpoint]=runs
        self.histories['/api/teacher/runs']=runs
        for path,target,endpoint in views:
            with self.subTest(path=path):
                self.page.goto(BASE+path)
                pager=self.page.locator('#'+target+'-pagination')
                expect(pager).to_be_visible();expect(pager).to_contain_text('Seite 1 von 4')
                self.assertEqual(self.page.locator('#'+target+' > *').count(),10)
                probes=sum(p.startswith('/api/maintenance/') for p,_ in self.calls)
                pager.get_by_role('button',name='Weiter',exact=True).click()
                expect(pager).to_contain_text('Seite 2 von 4')
                self.assertEqual(self.page.locator('#'+target+' > *').first.get_attribute('data-history-key'),runs[10]['id'])
                if path=='/installationsfolge':
                    self.page.evaluate('loadFresh()')
                    expect(pager).to_contain_text('Seite 2 von 4')
                if path=='/wartung':self.assertEqual(sum(p.startswith('/api/maintenance/') for p,_ in self.calls),probes)
                pager.get_by_role('button',name='Weiter',exact=True).click()
                expect(pager).to_contain_text('Seite 3 von 4')
                pager.get_by_role('button',name='Weiter',exact=True).click()
                expect(pager).to_contain_text('Seite 4 von 4')
                self.assertEqual(self.page.locator('#'+target+' > *').count(),5)
                expect(pager.get_by_role('button',name='Weiter',exact=True)).to_be_disabled()
        teacher=self.page.locator('#teacher-runs-pagination')
        teacher.get_by_role('button',name='Weiter',exact=True).click()
        expect(teacher).to_contain_text('Seite 2 von 4')
        self.histories['/api/logins']=[dict(time='2026-11-08T12:00:00+00:00',name='Testperson',account=f'test.{index}',
                                         role='reader',reason='signed_in',outcome='success') for index in range(35)]
        self.page.goto(BASE+'/anmeldungen')
        pager=self.page.locator('#login-log-rows-pagination')
        pager.get_by_role('button',name='Weiter',exact=True).click()
        expect(pager).to_contain_text('Seite 2 von 4')
        expect(self.page.locator('#login-log-rows').locator('tr').first).to_contain_text('test.10')
        self.assertFalse(self.errors,self.errors)

    def test_monitor_power_uses_shared_presence_in_all_views_and_survives_redraw(self):
        self.presence['devices']['LAB-01A']=dict(state='online',rtt_ms=2)
        for path in ('/','/sammelaktionen','/repo-software','/unterricht'):
            with self.subTest(path=path):
                self.page.goto(BASE+path)
                active=self.page.locator('.lab-room-monitor[data-presence-host="LAB-08A"]')
                inactive=self.page.locator('.lab-room-monitor[data-presence-host="LAB-08B"]')
                expect(active.locator('.lab-monitor-name')).to_have_text('08A')
                expect(active.locator('.lab-monitor-status')).to_have_text('')
                expect(inactive.locator('.lab-monitor-status')).to_have_text('')
                expect(inactive).to_have_attribute('title','LAB-08B · Keine Antwort auf Ping')
                expect(self.page.locator('.lab-room-monitor[data-presence-host="LAB-01A"] .lab-monitor-status')).to_have_text('')
                expect(self.page.locator('.lab-room-legend:visible')).to_have_count(1)
                self.assertFalse(self.page.locator('.presence-light').count())
                before=sum(url=='/api/presence' for url,_ in self.calls)
                if path=='/unterricht':
                    expect(self.page.locator('[data-classroom-host="LAB-01A"] .classroom-link')).to_have_text('Adapter deaktiviert')
                    self.page.locator('#classroom-search').fill('01A')
                    expect(self.page.locator('.lab-room-monitor .lab-monitor-status')).to_have_text('')
                elif path=='/repo-software':
                    self.page.evaluate('softwareRenderTargets()')
                    expect(active.locator('.lab-monitor-status')).to_have_text('')
                self.assertEqual(sum(url=='/api/presence' for url,_ in self.calls),before)
        self.assertFalse(any(payload is not None for _,payload in self.calls));self.assertFalse(self.errors,self.errors)

    def test_monitor_click_selects_only_its_client_and_failed_probe_is_unknown(self):
        self.page.goto(BASE+'/sammelaktionen')
        self.page.locator('#room-clear').click()
        active=self.page.locator('.lab-room-monitor[data-presence-host="LAB-08A"]')
        expect(active.locator('.lab-monitor-status')).to_have_text('')
        active.locator('svg').click()
        expect(self.page.get_by_role('checkbox',name='LAB-08A',exact=True)).to_be_checked()
        expect(self.page.get_by_role('checkbox',name='LAB-08B',exact=True)).not_to_be_checked()
        self.page.locator('.lab-room-monitor[data-presence-host="LAB-03A"] svg').click()
        expect(self.page.get_by_role('checkbox',name='LAB-03A',exact=True)).to_be_checked()
        expect(self.page.get_by_role('checkbox',name='LAB-03B',exact=True)).not_to_be_checked()
        self.presence['devices']['LAB-08A']=dict(state='unreachable',rtt_ms=None)
        self.page.evaluate('loadPresence()')
        expect(active.locator('.lab-monitor-status')).to_have_text('')
        self.context.route('**/api/presence',lambda route:route.fulfill(status=503,content_type='application/json',body=json.dumps(dict(error='Fixture: probe unavailable'))))
        self.page.evaluate('loadPresence()')
        expect(active.locator('.lab-monitor-status')).to_have_text('?')
        expect(self.page.locator('.lab-room-presence-stamp:visible')).to_have_text('Erreichbarkeit derzeit nicht verfügbar.')
        expect(self.page.get_by_role('checkbox',name='LAB-08A',exact=True)).to_be_checked()
        self.assertFalse(any(payload is not None for _,payload in self.calls));self.assertFalse(self.errors,self.errors)

    def test_presence_polling_stops_while_tab_is_hidden(self):
        self.page.clock.install()
        for path in ('/','/sammelaktionen','/repo-software','/unterricht'):
            with self.subTest(path=path):
                self.page.goto(BASE+path)
                expect(self.page.locator('[data-presence-host="LAB-08A"]')).to_have_attribute('data-state', 'online')
                self.page.wait_for_function('() => !presenceLoading')
                count = sum(url == '/api/presence' for url,_ in self.calls)
                self.page.evaluate("Object.defineProperty(document, 'hidden', {configurable:true,get:()=>true}); document.dispatchEvent(new Event('visibilitychange'))")
                self.page.clock.fast_forward(65000)
                self.assertEqual(sum(url == '/api/presence' for url,_ in self.calls), count)
                self.page.evaluate("Object.defineProperty(document, 'hidden', {configurable:true,get:()=>false}); document.dispatchEvent(new Event('visibilitychange'))")
                self.page.wait_for_function('() => !presenceLoading')
                self.assertEqual(sum(url == '/api/presence' for url,_ in self.calls), count+1)

    def test_installation_and_activity_polling_pause_and_resume_together(self):
        self.page.clock.install()
        self.page.goto(BASE+'/installationsfolge')
        expect(self.page.locator('#intake-gate')).to_be_visible()
        self.page.wait_for_function("backgroundPolls.get('fresh')?.pending && backgroundPolls.get('intake')?.pending && backgroundPolls.get('activity')?.pending")
        count = len(self.calls)
        self.page.evaluate("Object.defineProperty(document,'hidden',{configurable:true,get:()=>true});document.dispatchEvent(new Event('visibilitychange'))")
        self.page.clock.run_for(65000)
        self.assertEqual(len(self.calls),count)
        with self.page.expect_response('**/api/intake'):
            self.page.evaluate("Object.defineProperty(document,'hidden',{configurable:true,get:()=>false});document.dispatchEvent(new Event('visibilitychange'))")
        self.page.wait_for_function("backgroundPolls.get('fresh')?.pending && backgroundPolls.get('intake')?.pending && backgroundPolls.get('activity')?.pending")
        self.assertEqual(len(self.calls),count+3)
        count = len(self.calls)
        self.page.evaluate("window.dispatchEvent(new PageTransitionEvent('pagehide'))")
        self.page.clock.run_for(65000)
        self.assertEqual(len(self.calls),count)
        with self.page.expect_response('**/api/intake'):
            self.page.evaluate("window.dispatchEvent(new PageTransitionEvent('pageshow',{persisted:true}))")
        self.page.wait_for_function("backgroundPolls.get('fresh')?.pending && backgroundPolls.get('intake')?.pending && backgroundPolls.get('activity')?.pending")
        self.assertEqual(len(self.calls),count+3)
        self.assertFalse(any(payload is not None for _,payload in self.calls))
        self.assertFalse(self.errors,self.errors)

    def test_polling_backs_off_on_errors_and_stops_after_session_expiry(self):
        self.page.clock.install()
        attempts = []

        def failed(route):
            attempts.append(route.request.url)
            route.fulfill(status=401 if len(attempts)==4 else 503,content_type='application/json',
                          body=json.dumps(dict(error='Fixture: vorübergehend nicht verfügbar')))

        self.context.route('**/api/room/page/*', failed)
        self.page.goto(BASE+'/sammelaktionen')
        expect(self.page.locator('#room-status')).to_have_text('Fixture: vorübergehend nicht verfügbar')
        for delay, count in ((6000,2),(12000,3),(24000,4)):
            with self.page.expect_response('**/api/room/page/*'):
                self.page.clock.run_for(delay)
            self.assertEqual(len(attempts),count)
            self.page.wait_for_function("authenticationExpired" if count==4 else f"backgroundPolls.get('room')?.failures === {count}")
        expect(self.page.locator('#room-status')).to_contain_text('Anmeldung ist abgelaufen')
        count = len(self.calls)
        self.page.clock.run_for(180000)
        self.assertEqual(len(attempts),4)
        self.assertEqual(len(self.calls),count)
        self.assertFalse(self.errors,self.errors)

    def test_global_activity_banner_has_targets_time_and_link_and_clears_when_done(self):
        self.page.clock.install()
        self.activity = dict(busy=True,label='Herunterfahren',hosts=['LAB-03A','LAB-04B'],
                             started='2026-11-08T17:00:00+01:00',href='/sammelaktionen')
        self.page.goto(BASE+'/playbooks')
        expect(self.page.locator('#activity-banner')).to_be_visible()
        expect(self.page.locator('#activity-label')).to_have_text('Herunterfahren')
        expect(self.page.locator('#activity-targets')).to_have_text('LAB-03A, LAB-04B')
        expect(self.page.locator('#activity-started')).to_have_text('seit 17:00')
        expect(self.page.locator('#activity-link')).to_have_attribute('href','/cisco-lab/sammelaktionen')
        for width in (1440,390):
            self.page.set_viewport_size(dict(width=width,height=900))
            self.assertTrue(self.page.evaluate('document.documentElement.scrollWidth <= innerWidth'))
        self.activity = dict(busy=False)
        with self.page.expect_response('**/api/activity'):
            self.page.clock.run_for(30000)
        expect(self.page.locator('#activity-banner')).to_be_hidden()
        self.assertFalse(any(payload is not None for _,payload in self.calls))
        self.assertFalse(self.errors,self.errors)

    def test_backup_proofs_are_distinct_and_latest_stays_current_on_older_pages(self):
        restored = '2026-11-08T12:30:00+01:00'
        runs = [dict(id=f'{index:032x}',status='finished',started='2026-11-08T12:00:00+01:00',
                     file=('archive-'+'long-'*35 if index == 0 else f'archive-{index}')+'.tar.gz.gpg',
                     size=451630025,data_copy=True,copied=True,transfer=True,remote_sha256_verified=True,lab_system_copy=True)
                for index in range(12)]
        runs[0].update(remote_restore_verified_at=restored,confirmed_good_at=restored)
        runs[1].update(status='failed',copied=False,remote_sha256_verified=False,error='Externe Kopie fehlgeschlagen; lokales Archiv bleibt erhalten.')
        runs[2].update(restore_verified_at=restored)
        runs[3].update(pruned_at=restored)
        self.histories['/api/backup'] = runs
        self.page.goto(BASE+'/backup')
        expect(self.page.locator('#backup-latest-checks li')).to_have_count(3)
        expect(self.page.locator('#backup-latest-checks')).to_contain_text('Aus Mac-Kopie wiederhergestellt')
        expect(self.page.locator('#backup-latest-confirmed')).to_be_visible()
        expect(self.page.locator('#backup-runs > details[open]')).to_have_count(0)
        history = self.page.locator('#backup-runs > details')
        history.nth(1).locator('summary').click()
        expect(history.nth(1).locator('[data-state="good"]')).to_contain_text('Lokal gesichert')
        expect(history.nth(1).locator('.backup-run-detail')).to_contain_text('Keine externe Kopie bestätigt')
        expect(history.nth(1).locator('.notice')).to_contain_text('lokales Archiv bleibt erhalten')
        history.nth(2).locator('summary').click()
        expect(history.nth(2).locator('.backup-run-detail')).to_contain_text('Externe Wiederherstellung nicht nachgewiesen')
        expect(history.nth(2).get_by_role('button')).to_have_count(0)
        history.nth(3).locator('summary').click()
        expect(history.nth(3).locator('.backup-checks')).to_contain_text('Lokales Archiv entfernt')
        self.page.evaluate('loadBackups()')
        expect(history.nth(1)).to_have_attribute('open', '')
        history.nth(1).locator('summary').click()
        self.page.evaluate('loadBackups()')
        expect(history.nth(1)).not_to_have_attribute('open', '')
        history.first.locator('summary').click()
        for width in (1360,850,390):
            self.page.set_viewport_size({'width':width,'height':844})
            self.assertTrue(self.page.evaluate('document.documentElement.scrollWidth <= innerWidth'),width)
        self.page.locator('#backup-runs-pagination').get_by_role('button',name='Weiter',exact=True).click()
        expect(self.page.locator('#backup-runs-pagination')).to_contain_text('Seite 2 von 2')
        expect(self.page.locator('#backup-latest-file')).to_contain_text(runs[0]['file'])
        expect(self.page.locator('#backup-latest-confirmed')).to_be_visible()
        self.assertFalse(any(payload is not None for _,payload in self.calls))
        self.assertFalse(self.errors,self.errors)

    def test_catalog_sources_are_lazy_and_other_workflows_use_their_own_pages(self):
        self.page.goto(BASE+'/playbooks')
        expect(self.page.locator('#catalog-activity')).to_have_text('Status geladen')
        self.assertFalse(any(path=='/api/playbooks/firefox' for path,_ in self.calls))
        self.page.locator('#catalog-code-details summary').click()
        expect(self.page.locator('#catalog-code')).to_have_text('# Fixture firefox')
        self.page.locator('#catalog-job').select_option('appearance')
        expect(self.page.locator('#catalog-history')).to_be_hidden()
        expect(self.page.locator('#catalog-rollout')).to_be_hidden()
        expect(self.page.locator('#catalog-workflow-link')).to_have_attribute('href','/cisco-lab/desktopbilder')
        self.assertFalse(any(path=='/api/rollout/state/appearance' for path,_ in self.calls))
        for job,path in [('fresh','/installationsfolge'),('bootstrap','/installationsfolge'),('winbind','/installationsfolge'),('maintenance','/wartung')]:
            self.page.locator('#catalog-job').select_option(job)
            expect(self.page.locator('#catalog-workflow-link')).to_have_attribute('href','/cisco-lab'+path)
        self.page.locator('#catalog-job').select_option('serial')
        expect(self.page.locator('#catalog-pilot-link')).to_have_attribute('href','/cisco-lab/pilotdeploys#serial')
        self.page.locator('#catalog-pilot-link').click()
        # Real pilot catalogue includes serial; fixture only needs to prove that
        # arbitrary unknown fragments cannot produce a client action.
        expect(self.page.locator('#pilot-start')).to_be_disabled()
        self.assertFalse(any(payload is not None for _,payload in self.calls))
        self.assertFalse(self.errors,self.errors)

    def test_catalog_release_rechecks_current_approval_and_posts_only_the_confirmed_job(self):
        self.catalog_approvals['firefox']=dict(id='d'*32,host='LAB-08A',revision='a'*64)
        self.page.goto(BASE+'/playbooks')
        expect(self.page.locator('#catalog-rollout')).to_be_enabled()
        self.page.locator('#catalog-rollout').click()
        self.page.locator('#catalog-confirm button[value="cancel"]').click()
        self.assertFalse(any(payload is not None for _,payload in self.calls))
        self.page.locator('#catalog-rollout').click()
        self.catalog_approvals['firefox']=dict(id='e'*32,host='LAB-08A',revision='b'*64)
        self.page.evaluate('loadCatalogRelease("firefox")')
        self.page.locator('#catalog-confirm button[value="start"]').click()
        expect(self.page.locator('#catalog-status')).to_contain_text('Freigabe hat sich geändert')
        self.assertFalse(any(payload is not None for _,payload in self.calls))
        self.page.locator('#catalog-rollout').click()
        self.page.locator('#catalog-confirm button[value="start"]').click()
        expect(self.page.locator('#catalog-activity')).to_have_text('Verteilung läuft')
        self.assertEqual([(path,payload) for path,payload in self.calls if payload is not None],
                         [('/api/rollout/start',dict(job='firefox',run_id='e'*32,retry='',confirmed=True))])
        self.assertFalse(self.errors,self.errors)

    def test_catalog_closed_log_stays_closed_and_readers_can_read_sources_only(self):
        self.catalog_approvals['firefox']=dict(id='d'*32,host='LAB-08A',revision='a'*64)
        self.histories['/api/rollout/state/firefox']=[dict(id='e'*32,status='finished',started='2026-11-08T17:00:00+01:00',
            targets=['LAB-08A'],results={'LAB-08A':'Deploy erfolgreich.'},revision='a'*64,pending=[])]
        self.page.goto(BASE+'/playbooks')
        row=self.page.locator('#catalog-runs > details').first
        row.locator('summary').click()
        pending=[]
        self.page.route('**/api/appearance/rollout/log/*/*',lambda route:pending.append(route))
        row.get_by_role('button',name='Protokoll',exact=True).click()
        self.page.locator('#catalog-log-close').click()
        pending.pop().fulfill(content_type='application/json',body=json.dumps(dict(output='Late fixture output')))
        self.page.locator('#catalog-refresh').click()
        expect(row).to_have_attribute('open','')
        expect(self.page.locator('#catalog-log-panel')).to_be_hidden()
        self.role='reader';self.page.reload()
        expect(self.page.locator('#catalog-rollout')).to_be_disabled()
        self.page.locator('#catalog-code-details summary').click()
        expect(self.page.locator('#catalog-code')).to_contain_text('# Fixture firefox')
        self.page.locator('#catalog-refresh').click()
        expect(self.page.locator('#catalog-rollout')).to_be_disabled()
        for width in (1440,850,390):
            self.page.set_viewport_size(dict(width=width,height=844))
            self.assertTrue(self.page.evaluate('document.documentElement.scrollWidth <= innerWidth'),width)
        self.assertFalse(any(payload is not None for _,payload in self.calls))
        self.assertFalse(self.errors,self.errors)

    def test_catalog_selection_read_error_clears_old_results_and_recovers(self):
        self.histories['/api/rollout/state/firefox']=[dict(id='e'*32,status='finished',started='2026-11-08T17:00:00+01:00',targets=['LAB-08A'],results={},revision='a'*64,pending=[])]
        self.page.goto(BASE+'/playbooks')
        expect(self.page.locator('#catalog-runs > details')).to_have_count(1)
        self.page.route('**/api/rollout/state/serial/page/1',lambda route:route.fulfill(status=503,content_type='application/json',body=json.dumps(dict(error='Fixture status unavailable'))))
        self.page.locator('#catalog-job').select_option('serial')
        expect(self.page.locator('#catalog-activity')).to_have_text('Status nicht verfügbar')
        expect(self.page.locator('#catalog-runs > details')).to_have_count(0)
        expect(self.page.locator('#catalog-rollout')).to_be_disabled()
        self.page.unroute('**/api/rollout/state/serial/page/1')
        self.page.locator('#catalog-refresh').click()
        expect(self.page.locator('#catalog-activity')).to_have_text('Status geladen')
        expect(self.page.locator('#catalog-empty')).to_be_visible()
        self.assertFalse(any(payload is not None for _,payload in self.calls))
        self.assertFalse(self.errors,self.errors)

    def test_clients_search_and_reload_use_saved_data_and_probe_is_explicit(self):
        self.page.goto(BASE+'/schuelerclients')
        expect(self.page.locator('#client-rows > tr')).to_have_count(16)
        self.page.locator('#clients-search').fill('lab08a')
        expect(self.page.locator('#client-rows > tr')).to_have_count(1)
        expect(self.page.locator('#client-rows')).to_contain_text('Fix vorhanden')
        self.page.locator('#clients-reload').click()
        expect(self.page.locator('#clients-visible')).to_have_text('1 von 16 Clients')
        self.assertFalse(any(payload is not None for _,payload in self.calls))
        self.page.locator('#clients-search').fill('08B')
        expect(self.page.locator('#client-rows [data-label="Updates"]')).to_have_text('—')
        self.page.locator('#refresh').click()
        expect(self.page.locator('#clients-activity')).to_have_text('Abfrage läuft')
        self.client_report['refreshing']=False;self.page.evaluate('load()')
        expect(self.page.locator('#clients-activity')).to_have_text('Bericht geladen')
        self.assertEqual([(path,payload) for path,payload in self.calls if payload is not None],[('/api/refresh',{})])
        self.page.locator('#clients-search').fill('does-not-exist')
        expect(self.page.locator('#clients-empty')).to_be_visible()
        self.role='reader';self.page.reload()
        expect(self.page.locator('#refresh')).to_be_disabled()
        expect(self.page.locator('#clients-reload')).to_be_enabled()
        for width in (1440,850,390):
            self.page.set_viewport_size(dict(width=width,height=844))
            self.assertTrue(self.page.evaluate('document.documentElement.scrollWidth <= innerWidth'),width)
        self.assertFalse(self.errors,self.errors)

    def test_login_audit_preserves_unknown_identity_and_empty_state_on_reload(self):
        self.histories['/api/logins']=[dict(time='2026-11-08T12:40:00+00:00',account=None,name=None,outcome='denied',reason='provider_denied',role=None)]
        self.page.goto(BASE+'/anmeldungen')
        expect(self.page.locator('#login-log-rows')).to_contain_text('IServ hat keinen Namen übermittelt')
        expect(self.page.locator('#login-log-rows')).to_contain_text('kein Name übermittelt')
        for width in (1440,850,390):
            self.page.set_viewport_size(dict(width=width,height=844))
            self.assertTrue(self.page.evaluate('document.documentElement.scrollWidth <= innerWidth'),width)
        self.histories['/api/logins']=[]
        self.page.locator('#login-log-refresh').click()
        expect(self.page.locator('#login-log-empty')).to_be_visible()
        expect(self.page.locator('#login-log-activity')).to_have_text('Protokoll geladen')
        self.assertFalse(any(payload is not None for _,payload in self.calls))
        self.assertFalse(self.errors,self.errors)

    def test_pilot_preview_selection_confirmation_and_start_are_exact(self):
        self.page.goto(BASE+'/pilotdeploys#appearance')
        expect(self.page.locator('#pilot-job')).to_have_value('appearance')
        expect(self.page.locator('#pilot-preview-empty')).to_be_visible()
        self.page.locator('#pilot-host').select_option('LAB-08B')
        self.page.locator('#pilot-plan').click()
        expect(self.page.locator('#pilot-task-count')).to_have_text('2 Aufgaben')
        expect(self.page.locator('#pilot-preview-images')).to_contain_text('Fixture-Desktop.png')
        expect(self.page.locator('#pilot-start')).to_have_text('Auf LAB-08B starten')
        self.assertFalse(any(path=='/api/pilot/start' for path,_ in self.calls))
        self.page.locator('#pilot-start').click()
        expect(self.page.locator('#pilot-confirm-target')).to_contain_text('LAB-08B')
        expect(self.page.locator('#pilot-confirm-target')).to_contain_text('Fixture-Sperre.jpg')
        self.page.locator('#pilot-confirm button[value="cancel"]').click()
        self.assertFalse(any(path=='/api/pilot/start' for path,_ in self.calls))
        self.page.locator('#pilot-job').select_option('firefox')
        expect(self.page.locator('#pilot-action')).to_be_hidden()
        expect(self.page.locator('#pilot-preview-empty')).to_be_visible()
        self.page.locator('#pilot-plan').click()
        expect(self.page.locator('#pilot-preview-images')).to_be_hidden()
        self.page.locator('#pilot-start').click()
        self.page.locator('#pilot-confirm button[value="start"]').click()
        expect(self.page.locator('#pilot-output')).to_contain_text('Fixture: pilot output')
        expect(self.page.locator('#pilot-activity')).to_have_text('Pilot läuft')
        expect(self.page.locator('#pilot-plan')).to_be_disabled()
        self.assertEqual([(path,payload) for path,payload in self.calls if path=='/api/pilot/start'],
                         [('/api/pilot/start',dict(host='LAB-08B',job='firefox',revision='a'*64,token='b'*32))])
        self.assertFalse(self.errors,self.errors)

    def test_pilot_review_requires_every_check_and_records_only_the_selected_run(self):
        run=dict(id='d'*32,host='LAB-08A',job='firefox',status='succeeded',started='2026-11-08T17:00:00+01:00',revision='a'*64)
        self.histories['/api/pilot']=[run]
        self.page.goto(BASE+'/pilotdeploys')
        row=self.page.locator('#pilot-runs > details').first
        expect(row).not_to_have_attribute('open','')
        row.locator('summary').click()
        row.get_by_role('button',name='Praxistest erfassen').click()
        expect(self.page.locator('#pilot-review-accept')).to_be_disabled()
        expect(self.page.locator('#pilot-review-reject')).to_be_disabled()
        self.page.locator('#pilot-review-checks input').nth(0).check()
        expect(self.page.locator('#pilot-review-progress')).to_have_text('1 von 3 Prüfpunkten bestätigt')
        expect(self.page.locator('#pilot-review-accept')).to_be_disabled()
        self.page.locator('#pilot-review-checks input').nth(1).check()
        self.page.locator('#pilot-review-checks input').nth(2).check()
        self.page.locator('#pilot-review-note').fill('Vor Ort geprüft.')
        self.page.locator('#pilot-review-accept').click()
        expect(self.page.locator('#pilot-review')).not_to_be_visible()
        expect(row).to_contain_text('Abgenommen')
        self.assertEqual([(path,payload) for path,payload in self.calls if payload is not None],
                         [('/api/pilot/review',dict(run_id='d'*32,decision='accepted',checks=['homepage','tabs','policies'],note='Vor Ort geprüft.'))])
        self.assertFalse(self.errors,self.errors)

    def test_pilot_rejection_reason_and_server_error_preserve_review_inputs(self):
        self.histories['/api/pilot']=[dict(id='d'*32,host='LAB-08A',job='firefox',status='succeeded',started='2026-11-08T17:00:00+01:00',revision='a'*64)]
        self.page.goto(BASE+'/pilotdeploys')
        self.page.locator('#pilot-runs summary').click()
        self.page.get_by_role('button',name='Praxistest erfassen').click()
        self.page.locator('#pilot-review-note').fill('   ')
        expect(self.page.locator('#pilot-review-reject')).to_be_disabled()
        self.page.locator('#pilot-review-note').fill('Noch nicht korrekt.')
        self.pilot_review_error='Der Quellenstand hat sich geändert.'
        self.page.locator('#pilot-review-reject').click()
        expect(self.page.locator('#pilot-review-error')).to_contain_text('Quellenstand')
        expect(self.page.locator('#pilot-review-note')).to_have_value('Noch nicht korrekt.')
        self.pilot_review_error=None
        self.page.locator('#pilot-review-reject').click()
        expect(self.page.locator('#pilot-review')).not_to_be_visible()
        expect(self.page.locator('#pilot-runs')).to_contain_text('Abgelehnt')
        self.assertFalse(any(path in ('/api/pilot/start','/api/pilot/plan') for path,_ in self.calls))
        self.assertFalse(self.errors,self.errors)

    def test_pilot_stale_review_and_closed_logs_do_not_reopen_on_refresh(self):
        run=dict(id='d'*32,host='LAB-08A',job='firefox',status='succeeded',started='2026-11-08T17:00:00+01:00',revision='a'*64)
        self.histories['/api/pilot']=[run]
        self.page.goto(BASE+'/pilotdeploys')
        row=self.page.locator('#pilot-runs > details').first
        row.locator('summary').click()
        pending=[]
        self.page.route('**/api/pilot/log/*',lambda route:pending.append(route))
        row.get_by_role('button',name='Protokoll',exact=True).click()
        expect(self.page.locator('#pilot-log')).to_be_visible()
        self.page.locator('#pilot-log-close').click()
        pending.pop().fulfill(content_type='application/json',body=json.dumps(dict(output='Late fixture response')))
        self.page.locator('#pilot-refresh').click()
        expect(self.page.locator('#pilot-log')).to_be_hidden()
        expect(row).to_have_attribute('open','')
        row.get_by_role('button',name='Praxistest erfassen').click()
        for check in self.page.locator('#pilot-review-checks input').all():check.check()
        run['superseded']=True
        self.page.evaluate('loadPilots()')
        expect(self.page.locator('#pilot-review-accept')).to_be_disabled()
        expect(self.page.locator('#pilot-review-error')).to_contain_text('nicht mehr abgenommen')
        self.page.locator('#pilot-review-cancel').click()
        row.locator('summary').click()
        self.page.locator('#pilot-refresh').click()
        expect(row).not_to_have_attribute('open','')
        self.assertFalse(any(payload is not None for _,payload in self.calls))
        self.assertFalse(self.errors,self.errors)

    def test_pilot_read_error_recovers_and_readers_keep_all_actions_disabled(self):
        self.page.route('**/api/pilot/page/1',lambda route:route.fulfill(status=503,content_type='application/json',body=json.dumps(dict(error='Fixture unavailable'))))
        self.page.goto(BASE+'/pilotdeploys')
        expect(self.page.locator('#pilot-activity')).to_have_text('Status nicht verfügbar')
        expect(self.page.locator('#pilot-plan')).to_be_disabled()
        self.page.unroute('**/api/pilot/page/1')
        self.page.locator('#pilot-refresh').click()
        expect(self.page.locator('#pilot-plan')).to_be_enabled()
        expect(self.page.locator('#pilot-status')).to_have_text('')
        self.role='reader'
        self.histories['/api/pilot']=[dict(id='d'*32,host='LAB-08A',job='firefox',status='succeeded',started='2026-11-08T17:00:00+01:00',revision='a'*64)]
        self.page.reload()
        expect(self.page.locator('#pilot-runs')).to_contain_text('Abnahme offen')
        expect(self.page.get_by_role('button',name='Praxistest erfassen')).to_have_count(0)
        for width in (1440,850,390):
            self.page.set_viewport_size(dict(width=width,height=844))
            self.assertTrue(self.page.evaluate('document.documentElement.scrollWidth <= innerWidth'),width)
        self.page.locator('#pilot-refresh').click()
        for id in ('pilot-plan','pilot-host','pilot-job','pilot-start'):
            expect(self.page.locator('#'+id)).to_be_disabled()
        self.assertFalse(any(payload is not None for _,payload in self.calls))
        self.assertFalse(self.errors,self.errors)

    def test_images_preview_draft_refresh_and_save_preserve_the_pilot_gate(self):
        self.appearance_approval=dict(id='d'*32,host='LAB-08A',revision='a'*64)
        self.histories['/api/appearance/rollout']=[dict(id='e'*32,status='finished',started='2026-11-08T17:00:00+01:00',
                    targets=['LAB-08A'],results={},revision='a'*64,pending=['LAB-08A'])]
        self.page.goto(BASE+'/desktopbilder')
        expect(self.page.locator('#appearance-desktop-preview')).to_be_visible()
        expect(self.page.locator('#appearance-lock-preview')).to_be_visible()
        expect(self.page.locator('#appearance-release-start')).to_be_enabled()
        self.page.locator('#appearance-desktop').select_option('custom.png')
        expect(self.page.locator('[data-appearance-retry]')).to_be_disabled()
        expect(self.page.locator('#appearance-activity')).to_have_text('Ungespeicherte Auswahl')
        expect(self.page.locator('#appearance-desktop-caption')).to_have_text('Vorschau · custom.png')
        expect(self.page.locator('#appearance-release-start')).to_be_disabled()
        expect(self.page.locator('#appearance-pilot-link')).to_be_hidden()
        self.page.locator('#appearance-refresh').click()
        expect(self.page.locator('#appearance-desktop')).to_have_value('custom.png')
        expect(self.page.locator('#appearance-status')).to_contain_text('Auswahl bleibt erhalten')
        self.assertFalse(any(payload is not None for _,payload in self.calls))
        self.page.locator('#appearance-save').click()
        expect(self.page.locator('#appearance-status')).to_contain_text('Auf Clients wurde noch nichts geändert')
        expect(self.page.locator('#appearance-desktop-caption')).to_have_text('Gespeichert · custom.png')
        expect(self.page.locator('#appearance-release-badge')).to_have_text('Abnahme offen')
        expect(self.page.locator('#appearance-release-start')).to_be_disabled()
        self.assertEqual([(path,payload) for path,payload in self.calls if payload is not None],
                         [('/api/appearance/select',dict(desktop='custom.png',lock=''))])
        self.page.locator('#appearance-pilot-link').click()
        expect(self.page.locator('#pilot-job')).to_have_value('appearance')
        self.assertFalse(self.errors,self.errors)

    def test_images_release_read_failure_stays_visible_and_refresh_recovers(self):
        self.page.route('**/api/appearance/rollout/page/1',lambda route:route.fulfill(status=503,content_type='application/json',body=json.dumps(dict(error='Fixture temporarily unavailable'))))
        self.page.goto(BASE+'/desktopbilder')
        expect(self.page.locator('#appearance-desktop-preview')).to_be_visible()
        expect(self.page.locator('#appearance-activity')).to_have_text('Status nicht verfügbar')
        expect(self.page.locator('#appearance-release-start')).to_be_disabled()
        self.page.unroute('**/api/appearance/rollout/page/1')
        self.page.locator('#appearance-refresh').click()
        expect(self.page.locator('#appearance-activity')).to_have_text('Bereit')
        expect(self.page.locator('#appearance-save')).to_be_enabled()
        self.assertFalse(any(payload is not None for _,payload in self.calls))
        self.assertFalse(self.errors,self.errors)

    def test_images_compact_history_log_close_and_exact_confirmed_release(self):
        self.appearance_approval=dict(id='d'*32,host='LAB-08A',revision='a'*64)
        self.histories['/api/appearance/rollout']=[dict(id='e'*32,status='finished',started='2026-11-08T17:00:00+01:00',
                    targets=['LAB-08A'],results={'LAB-08A':'Bild installiert.'},revision='a'*64,pending=[])]
        self.page.goto(BASE+'/desktopbilder')
        row=self.page.locator('#appearance-release-runs > details').first
        expect(row).not_to_have_attribute('open','')
        row.locator('summary').click()
        row.get_by_role('button',name='Protokoll',exact=True).click()
        expect(self.page.locator('#appearance-release-output')).to_contain_text('Fixture: image deployed')
        self.page.locator('#appearance-release-close').click()
        expect(self.page.locator('#appearance-release-log')).to_be_hidden()
        self.page.evaluate('loadAppearanceRelease()')
        expect(self.page.locator('#appearance-release-log')).to_be_hidden()
        expect(row).to_have_attribute('open','')
        row.locator('summary').click()
        self.page.evaluate('loadAppearanceRelease()')
        expect(row).not_to_have_attribute('open','')
        self.page.locator('#appearance-release-start').click()
        expect(self.page.locator('#appearance-release-targets')).to_contain_text('LAB-08A')
        self.page.locator('#appearance-release-confirm button[value="cancel"]').click()
        self.assertFalse(any(payload is not None for _,payload in self.calls))
        self.page.locator('#appearance-release-refresh').uncheck()
        self.page.locator('#appearance-release-start').click()
        self.page.locator('#appearance-release-confirm-start').click()
        expect(self.page.locator('#appearance-activity')).to_have_text('Verteilung läuft')
        self.assertEqual([(path,payload) for path,payload in self.calls if payload is not None],
                         [('/api/appearance/rollout/start',dict(run_id='d'*32,refresh=False,confirmed=True,retry=''))])
        self.assertFalse(self.errors,self.errors)

    def test_images_failed_preview_can_be_reloaded_without_losing_the_draft(self):
        self.page.route('**/api/appearance/preview/'+'b'*64,lambda route:route.fulfill(status=503,body='Fixture unavailable'))
        self.page.goto(BASE+'/desktopbilder')
        self.page.locator('#appearance-desktop').select_option('custom.png')
        expect(self.page.locator('#appearance-desktop-placeholder')).to_contain_text('Bitte Bilder neu laden')
        self.page.unroute('**/api/appearance/preview/'+'b'*64)
        self.page.locator('#appearance-refresh').click()
        expect(self.page.locator('#appearance-desktop-preview')).to_be_visible()
        expect(self.page.locator('#appearance-desktop')).to_have_value('custom.png')
        self.assertFalse(any(payload is not None for _,payload in self.calls))
        self.assertFalse(self.errors,self.errors)

    def test_images_multiple_uploads_keep_draft_and_do_not_save_or_deploy(self):
        self.page.goto(BASE+'/desktopbilder')
        self.page.locator('#appearance-desktop').select_option('custom.png')
        expect(self.page.locator('#appearance-upload-start')).to_be_disabled()
        self.page.locator('#appearance-upload-files').set_input_files([
            dict(name='Neues Bild ä.png',mimeType='image/png',buffer=b'fixture png'),
            dict(name='Hintergrund.jpg',mimeType='image/jpeg',buffer=b'fixture jpg'),
            dict(name='unwanted.svg',mimeType='image/svg+xml',buffer=b'<svg/>')])
        self.page.locator('#appearance-upload-start').click()
        expect(self.page.locator('#appearance-upload-status')).to_contain_text('2 Bilder hochgeladen')
        expect(self.page.locator('#appearance-upload-status')).to_contain_text('unwanted.svg: Bitte PNG oder JPEG')
        expect(self.page.locator('#appearance-desktop')).to_have_value('custom.png')
        expect(self.page.locator('#appearance-desktop option[value="Neues Bild ä.png"]')).to_have_count(1)
        expect(self.page.locator('#appearance-upload-start')).to_be_disabled()
        posts=[(path,payload) for path,payload in self.calls if payload is not None]
        self.assertEqual([path for path,_ in posts],['/api/appearance/upload']*2)
        self.assertEqual([payload['size'] for _,payload in posts],[11,11])
        self.assertFalse(self.errors,self.errors)

    def test_images_readers_see_previews_and_mobile_layout_has_no_overflow(self):
        self.role='reader'
        self.appearance_approval=dict(id='d'*32,host='LAB-08A',revision='a'*64)
        self.page.goto(BASE+'/desktopbilder')
        expect(self.page.locator('#appearance-desktop-preview')).to_be_visible()
        for id in ('appearance-desktop','appearance-lock','appearance-save','appearance-release-start','appearance-upload-files','appearance-upload-start'):
            expect(self.page.locator('#'+id)).to_be_disabled()
        expect(self.page.locator('#appearance-refresh')).to_be_enabled()
        self.page.locator('#appearance-refresh').click()
        expect(self.page.locator('#appearance-desktop')).to_be_disabled()
        for width in (1440,850,390):
            self.page.set_viewport_size(dict(width=width,height=844))
            self.assertTrue(self.page.evaluate('document.documentElement.scrollWidth <= innerWidth'),width)
        self.assertFalse(any(payload is not None for _,payload in self.calls))
        self.assertFalse(self.errors,self.errors)

    def test_backup_defaults_to_app_and_browser_remembers_only_target_preferences(self):
        self.page.goto(BASE+'/backup')
        def selected():return self.page.locator('[data-backup-target]:checked').evaluate_all('nodes=>nodes.map(node=>node.dataset.backupTarget)')
        self.assertEqual(selected(),['apps'])
        self.page.locator('[data-backup-target="mac"]').check()
        self.page.locator('[data-backup-target="download"]').check()
        self.page.locator('[data-backup-target="apps"]').uncheck()
        self.page.reload()
        expect(self.page.locator('#backup-start')).to_be_enabled()
        self.assertEqual(selected(),['mac','download'])
        saved=self.page.evaluate('localStorage.getItem(backupPreferenceKey)')
        self.assertEqual(json.loads(saved),['mac','download'])
        self.page.evaluate("localStorage.setItem(backupPreferenceKey,'{broken')")
        self.page.reload();expect(self.page.locator('#backup-start')).to_be_enabled()
        self.assertEqual(selected(),['apps'])
        self.context.add_init_script("Storage.prototype.getItem = () => { throw new Error('blocked'); }; Storage.prototype.setItem = () => { throw new Error('blocked'); };")
        self.page.reload();expect(self.page.locator('#backup-start')).to_be_enabled()
        self.assertEqual(selected(),['apps'])
        self.page.locator('[data-backup-target="download"]').check()
        self.assertEqual(selected(),['apps','download'])
        self.assertFalse(any(payload is not None for _,payload in self.calls))
        self.assertFalse(self.errors,self.errors)

    def test_backup_cleanup_previews_cancel_and_progress_without_starting_a_backup(self):
        self.histories['/api/backup']=[dict(id='f'*32,status='finished',started='2026-11-08T16:00:00+01:00',data_copy=True,copied=True,confirmed_good_at='2026-11-08T16:10:00+01:00')]
        self.cleanup_candidates=[dict(id='a'*32,size=100,apps=True,mac=True),dict(id='b'*32,size=200,apps=True,mac=False)]
        self.page.goto(BASE+'/backup')
        self.page.locator('#backup-cleanup').click()
        expect(self.page.locator('#catalog-confirm')).to_be_visible()
        expect(self.page.locator('#catalog-confirm')).to_contain_text('2 ältere Sicherungen')
        expect(self.page.locator('#catalog-confirm')).to_contain_text('bleiben geschützt')
        self.assertFalse(any(payload is not None for _,payload in self.calls))
        self.page.locator('#catalog-confirm button[value="cancel"]').click()
        self.assertFalse(any(payload is not None for _,payload in self.calls))
        self.page.locator('#backup-cleanup').click()
        self.page.locator('#catalog-confirm button[value="start"]').click()
        expect(self.page.locator('#backup-activity')).to_have_text('Bereinigung läuft')
        expect(self.page.locator('#backup-start')).to_be_disabled()
        expect(self.page.locator('#backup-cleanup')).to_be_disabled()
        self.backup_cleanup.update(status='finished',processed=2,removed=2,bytes=dict(apps=300,mac=100))
        self.page.evaluate('loadBackups()')
        expect(self.page.locator('#backup-cleanup-status')).to_contain_text('2 ältere Sicherungen bereinigt')
        expect(self.page.locator('#backup-start')).to_be_enabled()
        expect(self.page.locator('#backup-latest-confirmed')).to_be_visible()
        self.assertEqual([(path,payload) for path,payload in self.calls if payload is not None],
                         [('/api/backup/cleanup',dict(token='a'*64,confirmed=True))])
        self.cleanup_candidates=[]
        self.page.locator('#backup-cleanup').click()
        expect(self.page.locator('#backup-cleanup-status')).to_contain_text('Keine älteren')
        expect(self.page.locator('#catalog-confirm')).to_be_visible()
        expect(self.page.locator('#catalog-confirm-title')).to_have_text('Sicherungen bereits bereinigt')
        expect(self.page.locator('#catalog-confirm-text')).to_contain_text('bleiben als Verlauf sichtbar')
        expect(self.page.locator('#catalog-confirm button[value="start"]')).to_be_hidden()
        self.page.get_by_role('button',name='Schließen',exact=True).click()
        expect(self.page.locator('#catalog-confirm')).not_to_be_visible()
        # A later actionable preview must restore its confirmation controls.
        self.cleanup_candidates=[dict(id='b'*32,size=200,apps=True,mac=False)]
        self.page.locator('#backup-cleanup').click()
        expect(self.page.locator('#catalog-confirm button[value="start"]')).to_be_visible()
        expect(self.page.locator('#catalog-confirm button[value="cancel"]')).to_have_text('Abbrechen')
        self.page.locator('#catalog-confirm button[value="cancel"]').click()
        self.assertEqual(sum(payload is not None for _,payload in self.calls),1)
        self.page.set_viewport_size({'width':390,'height':844})
        self.assertTrue(self.page.evaluate('document.documentElement.scrollWidth <= innerWidth'))
        self.role='reader';self.page.reload()
        expect(self.page.locator('#backup-cleanup')).to_be_hidden()
        self.assertFalse(self.errors,self.errors)

    def test_backup_cleanup_immediate_feedback_and_read_error_are_visible(self):
        self.page.goto(BASE+'/backup')
        pending=[]
        self.page.route('**/api/backup/cleanup',lambda route:pending.append(route))
        self.page.locator('#backup-cleanup').click()
        expect(self.page.locator('#backup-cleanup-status')).to_have_text('Ältere Sicherungen werden geprüft …')
        expect(self.page.locator('#backup-cleanup')).to_be_disabled()
        self.assertEqual(len(pending),1)
        pending.pop().fulfill(status=503,content_type='application/json',body=json.dumps(dict(error='Vorschau vorübergehend nicht verfügbar.')))
        expect(self.page.locator('#catalog-confirm')).to_be_visible()
        expect(self.page.locator('#catalog-confirm-title')).to_have_text('Bereinigung konnte nicht geprüft werden')
        expect(self.page.locator('#catalog-confirm-text')).to_contain_text('Vorschau vorübergehend nicht verfügbar.')
        expect(self.page.locator('#catalog-confirm button[value="start"]')).to_be_hidden()
        self.page.get_by_role('button',name='Schließen',exact=True).click()
        expect(self.page.locator('#backup-cleanup')).to_be_enabled()
        self.assertFalse(any(payload is not None for _,payload in self.calls))
        self.assertFalse(self.errors,self.errors)

    def test_backup_destinations_are_independent_and_start_resets_history_without_key_confirmation(self):
        self.backup_transfer_available = False
        self.histories['/api/backup'] = []
        self.page.goto(BASE+'/backup')
        expect(self.page.locator('#backup-empty')).to_be_visible()
        expect(self.page.locator('#backup-activity')).to_have_text('Bereit')
        expect(self.page.locator('#backup-confirm')).to_have_count(0)
        expect(self.page.locator('[data-backup-target="mac"]')).to_be_disabled()
        expect(self.page.locator('[data-backup-target="mac"]')).not_to_be_checked()
        expect(self.page.locator('#backup-start')).to_be_enabled()
        self.page.locator('[data-backup-target="apps"]').uncheck()
        expect(self.page.locator('#backup-start')).to_be_disabled()
        self.page.locator('[data-backup-target="download"]').check()
        expect(self.page.locator('#backup-start')).to_be_enabled()
        self.backup_transfer_available = True
        self.histories['/api/backup'] = [dict(id=f'{index:032x}',status='finished',started='2026-11-08T12:00:00+01:00') for index in range(12)]
        self.page.evaluate('loadBackups()')
        expect(self.page.locator('#backup-start')).to_be_enabled()
        expect(self.page.locator('[data-backup-target="mac"]')).to_be_enabled()
        self.page.locator('#backup-runs-pagination').get_by_role('button',name='Weiter',exact=True).click()
        expect(self.page.locator('#backup-runs-pagination')).to_contain_text('Seite 2 von 2')
        self.page.locator('#backup-start').click()
        expect(self.page.locator('#backup-activity')).to_have_text('Sicherung läuft')
        expect(self.page.locator('#backup-runs-pagination')).to_contain_text('Seite 1 von 2')
        expect(self.page.locator('#backup-start')).to_be_disabled()
        expect(self.page.locator('#backup-latest-checks')).to_contain_text('ZIP-Download ausstehend')
        expect(self.page.locator('#backup-latest-checks')).to_contain_text('Keine Mac-Kopie beauftragt')
        self.assertEqual([(path,payload) for path,payload in self.calls if payload is not None],
                         [('/api/backup/start',dict(targets=['download']))])
        self.histories['/api/backup'][0].update(status='finished',file='fixture.tar.gz.gpg',download_available=True,download_ready_at='2026-11-08T15:01:00+01:00')
        # Intercept the browser download intent; binary transport is covered by
        # the real loopback HTTP test, without an external fixture-host download.
        self.page.evaluate('''() => {
          window.downloadIntents = [];
          document.addEventListener('click', event => {
            const link = event.target.closest('#backup-download-ready');
            if (link) { event.preventDefault(); window.downloadIntents.push({href:link.href,name:link.download}); }
          }, true);
        }''')
        self.page.evaluate('loadBackups()')
        self.assertEqual(self.page.evaluate('downloadIntents'),[dict(href=BASE+'/api/backup/download/'+'e'*32,name='fixture.zip')])
        expect(self.page.locator('#backup-download-ready')).to_be_visible()
        self.page.evaluate('loadBackups()')
        self.assertEqual(self.page.evaluate('downloadIntents.length'),1)
        self.assertFalse(self.errors,self.errors)

    def test_backup_restore_confirmation_requires_recorded_remote_restore_and_dialog(self):
        self.histories['/api/backup'] = [dict(id='f'*32,status='finished',started='2026-11-08T12:00:00+01:00',
            file='fixture.tar.gz.gpg',data_copy=True,copied=True,remote_restore_verified_at='2026-11-08T12:30:00+01:00')]
        self.page.goto(BASE+'/backup')
        self.page.locator('#backup-runs summary').click()
        confirm = self.page.locator('#backup-runs').get_by_role('button',name='Wiederherstellung bestätigen')
        confirm.click()
        expect(self.page.locator('#catalog-confirm')).to_be_visible()
        self.assertFalse(any(payload is not None for _,payload in self.calls))
        self.page.locator('#catalog-confirm button[value="cancel"]').click()
        self.assertFalse(any(payload is not None for _,payload in self.calls))
        confirm.click()
        self.page.locator('#catalog-confirm button[value="start"]').click()
        expect(self.page.locator('#backup-latest-confirmed')).to_be_visible()
        expect(confirm).to_have_count(0)
        self.assertIn(('/api/backup/confirm',dict(run_id='f'*32,confirmed=True)),self.calls)
        self.role='reader'
        self.histories['/api/backup'][0].pop('confirmed_good_at')
        self.page.reload()
        self.page.locator('#backup-runs summary').click()
        expect(self.page.locator('[data-backup-target="apps"]')).to_be_disabled()
        expect(self.page.locator('[data-backup-target="download"]')).to_be_disabled()
        expect(self.page.locator('#backup-start')).to_be_disabled()
        expect(confirm).to_be_disabled()
        self.page.evaluate('loadBackups()')
        expect(confirm).to_be_disabled()
        self.assertFalse(self.errors,self.errors)

    def test_collegium_can_run_room_actions_and_other_administration_stays_disabled(self):
        self.role = 'reader'
        self.page.goto(BASE+'/sammelaktionen')
        expect(self.page.locator('.reader-notice')).to_be_visible()
        expect(self.page.locator('nav a[href$="/anmeldungen"]')).to_be_hidden()
        expect(self.page.locator('#room-prepare')).to_be_enabled()
        expect(self.page.locator('input[data-room-host="LAB-03A"]')).to_be_enabled()
        expect(self.page.locator('#room-all-students')).to_be_enabled()
        group=self.page.locator('.room-team').filter(has=self.page.locator('[data-room-team="03"]'))
        expect(group.locator('button')).to_be_enabled()
        before=group.locator('input:checked').count()
        group.click(position={'x':3,'y':3})
        self.assertEqual(group.locator('input:checked').count(),2-before)
        self.page.locator('#room-clear').click()
        self.page.locator('input[data-room-host="LAB-03A"]').check()
        self.page.locator('input[data-room-host="LAB-04B"]').check()
        self.page.locator('#room-action').select_option('poweroff')
        self.page.evaluate('loadRoom()')
        expect(self.page.locator('#room-prepare')).to_be_enabled()
        self.page.locator('#room-prepare').click()
        expect(self.page.locator('#room-confirm-hosts .badge')).to_have_count(2)
        self.assertFalse(any(path == '/api/room/start' for path,_ in self.calls))
        self.page.locator('#room-confirm button[value="cancel"]').click()
        self.assertFalse(any(path == '/api/room/start' for path,_ in self.calls))
        self.page.locator('#room-prepare').click()
        with self.page.expect_response('**/api/room/start'):
            self.page.locator('#room-confirm button[value="start"]').click()
        self.assertIn(('/api/room/start',dict(action='poweroff',targets=['LAB-03A','LAB-04B'],confirmed=True)), self.calls)
        result = self.page.evaluate('''async () => {
          try {await request('/api/refresh', {}); return 'unexpected';}
          catch(error){return error.message;}
        }''')
        self.assertIn('Lesezugriff', result)
        self.assertFalse(any(path == '/api/refresh' for path, _ in self.calls))
        expect(self.page.locator('.app-footer')).to_contain_text('2026 - Marc Schulz')
        expect(self.page.locator('.footer-credit a')).to_have_attribute('aria-label', 'E-Mail an Marc Schulz')
        expect(self.page.locator('.footer-credit a')).to_have_text('')
        self.assertEqual(self.page.locator('.app-footer a svg').count(), 3)
        self.assertEqual(self.page.locator('.app-footer a[href^="https://github.com"]').get_attribute('target'), '_blank')
        self.assertEqual(self.page.locator('.app-footer a[href^="https://mail896.github.io"]').get_attribute('target'), '_blank')
        self.ready = ['LAB-08B']
        self.page.goto(BASE+'/installationsfolge')
        expect(self.page.locator('#fresh-targets input[data-host="LAB-08B"]')).to_be_disabled()
        expect(self.page.locator('#intake-read')).to_be_disabled()
        expect(self.page.locator('#fresh-start')).to_be_disabled()
        self.page.goto(BASE+'/wartung')
        expect(self.page.locator('#maintenance-refresh')).to_be_disabled()
        expect(self.page.locator('#maintenance-start')).to_be_disabled()
        self.assertFalse(self.errors, self.errors)


if __name__ == '__main__': unittest.main()
