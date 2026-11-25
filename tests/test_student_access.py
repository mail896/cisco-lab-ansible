"""Student role: only the two task generators, no saving, own drafts and task audit."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from urllib.parse import parse_qs, urlencode, urlsplit

from dashboard import auth
from dashboard import exercises as exercise_module
from dashboard.cli_exercises import CliExercises, DEFAULT_TEXT as CLI_TEXT
from dashboard.exercises import DEFAULT_TEXT, Exercises
from dashboard.login_audit import TaskAudit
from tests import test_dashboard_auth as auth_fixtures

STUDENT_UUID = '33333333-3333-4333-8333-333333333333'
STUDENT = dict(account='schueler.test', name='Testschülerin', source='oidc', uuid=STUDENT_UUID, role='student')
TEACHER = dict(account='teacher', name='Lehrkraft', source='oidc', uuid='fixture-one', role='reader')


def student_info(roles=('student',), groups=()):
    return {'sub': 'fixture-student', 'iserv:uuid': STUDENT_UUID, 'preferred_username': 'schueler.test',
            'name': 'Testschülerin', 'iserv:groups': list(groups), 'iserv:roles': list(roles) if isinstance(roles, tuple) else roles}


class StudentRoleTests(unittest.TestCase):
    def test_iserv_student_role_gives_student_access_and_kollegium_wins(self):
        config = auth_fixtures.settings()
        for roles in (['student'], [{'id': 'student', 'name': 'Schüler'}], [{'name': 'ROLE_STUDENT'}],
                      {'roles': [{'id': 'student'}]}):
            self.assertEqual(auth.identity(student_info(roles), config)['role'], 'student', roles)
        both = student_info(['student'], [{'name': 'Kollegium'}])
        self.assertEqual(auth.identity(both, config)['role'], 'reader')
        for roles in ([], ['teacher'], ['students-extra'], 'student', None):
            with self.assertRaises(auth.Denied):
                auth.identity(student_info(roles), config)
        # A group called "student" is not the IServ role.
        with self.assertRaises(auth.Denied):
            auth.identity(student_info([], [{'name': 'student'}]), config)

    def test_login_requests_roles_and_invalid_scope_falls_back_for_staff(self):
        provider = auth.Provider(auth_fixtures.settings(), fetch=lambda *a, **k: {'authorization_endpoint': 'https://school.example.org/iserv/oauth/v2/auth'})
        provider.metadata = lambda: {'authorization_endpoint': 'https://school.example.org/iserv/oauth/v2/auth'}
        url = provider.begin(dict(state='s', nonce='n', verifier='v'*48))
        self.assertIn('iserv:roles', parse_qs(urlsplit(url).query)['scope'][0])
        root = Path(self.enterContext(TemporaryDirectory()))
        lab = auth.Auth(root/'auth', auth_fixtures.settings(), provider)
        tid, url = lab.begin(); state = parse_qs(urlsplit(url).query)['state'][0]
        with self.assertRaises(auth.AuthError):
            lab.complete(tid, urlencode(dict(state=state, error='invalid_scope')))
        self.assertEqual(provider.scopes, auth.BASE_SCOPES)
        tid, url = lab.begin()
        self.assertNotIn('iserv:roles', parse_qs(urlsplit(url).query)['scope'][0])


class StudentStoreTests(unittest.TestCase):
    def setUp(self):
        self.store = Exercises(Path(self.enterContext(TemporaryDirectory())))

    def test_student_sees_no_collection_and_cannot_open_or_save_teacher_tasks(self):
        saved = self.store.create({}, DEFAULT_TEXT, TEACHER)
        state = self.store.state(STUDENT)
        self.assertEqual(state['runs'], []); self.assertEqual(state['pagination']['total'], 0)
        self.assertEqual(state['deletion']['count'], 0)
        with self.assertRaises(FileNotFoundError):
            self.store.task(saved['id'], STUDENT)
        with self.assertRaises(FileNotFoundError):
            self.store.revise(saved['id'], DEFAULT_TEXT, STUDENT)
        draft = self.store.preview({}, DEFAULT_TEXT, STUDENT)
        self.assertFalse(draft['saved']); self.assertEqual(self.store.task(draft['id'], STUDENT)['id'], draft['id'])
        for action in (lambda: self.store.save(draft['id'], DEFAULT_TEXT, STUDENT),
                       lambda: self.store.create({}, DEFAULT_TEXT, STUDENT),
                       lambda: self.store.template('', DEFAULT_TEXT, {}, STUDENT),
                       lambda: self.store.standard(DEFAULT_TEXT, {}, STUDENT),
                       lambda: self.store.delete('tasks', saved['id'], STUDENT),
                       lambda: self.store.delete_all('', True, STUDENT)):
            with self.assertRaises(PermissionError):
                action()
        self.assertEqual(len(list((self.store.directory/'tasks').glob('*.json'))), 1)
        with self.assertRaises(FileNotFoundError):
            self.store.task(draft['id'], TEACHER)

    def test_student_drafts_have_own_bounded_pool_and_previews_are_throttled(self):
        teacher_draft = self.store.preview({}, DEFAULT_TEXT, TEACHER)
        for index in range(exercise_module.STUDENT_DRAFTS_PER_OWNER + 3):
            last = self.store.preview({}, DEFAULT_TEXT, STUDENT)
        self.assertEqual(len(self.store.student_drafts), exercise_module.STUDENT_DRAFTS_PER_OWNER)
        self.assertIn(teacher_draft['id'], self.store.drafts)
        self.assertNotIn(last['id'], self.store.drafts)
        for _ in range(exercise_module.STUDENT_PREVIEWS_PER_MINUTE - exercise_module.STUDENT_DRAFTS_PER_OWNER - 3):
            self.store.preview({}, DEFAULT_TEXT, STUDENT)
        with self.assertRaises(PermissionError):
            self.store.preview({}, DEFAULT_TEXT, STUDENT)
        self.store.preview({}, DEFAULT_TEXT, TEACHER)

    def test_linux_generator_follows_the_same_student_rules(self):
        store = CliExercises(self.store.directory/'cli')
        draft = store.preview({}, CLI_TEXT, STUDENT)
        self.assertEqual(store.task(draft['id'], STUDENT)['id'], draft['id'])
        with self.assertRaises(PermissionError):
            store.save(draft['id'], CLI_TEXT, STUDENT)


class TaskAuditTests(unittest.TestCase):
    def test_task_audit_records_identity_and_rejects_unknown_events(self):
        root = Path(self.enterContext(TemporaryDirectory()))/'audit'
        log = TaskAudit(root)
        log.append('network', 'preview', 'a'*32, 'LAB-Routing Übungen · Anna / Ben', STUDENT)
        log.append('linux', 'export_zip', 'b'*32, 'Linux · Team 1', TEACHER)
        rows = log.records()
        self.assertEqual([row['action'] for row in rows], ['export_zip', 'preview'])
        self.assertEqual(rows[1]['name'], 'Testschülerin'); self.assertEqual(rows[1]['role'], 'student')
        for bad in (('network', 'delete', 'a'*32), ('other', 'preview', 'a'*32), ('network', 'preview', 'x')):
            with self.assertRaises(ValueError):
                log.append(*bad, '', STUDENT)


class StudentRouteTests(unittest.TestCase):
    setUp = auth_fixtures.SessionAndRouteTests.setUp
    handler = auth_fixtures.SessionAndRouteTests.handler

    def login_student(self):
        self.provider.complete.return_value = dict(identity=auth.identity(student_info(), self.config), subject='fixture',
                                                   access='fixture-token', expires=self.now[0]+300)
        tid, url = self.auth.begin(); state = parse_qs(urlsplit(url).query)['state'][0]
        return self.auth.complete(tid, urlencode(dict(state=state, code='fixture-code')))

    def request(self, path, method='GET', body=None):
        handler = self.handler(path, method, self.sid, self.session['csrf'] if method == 'POST' else None, body)
        handler.server.exercises = self.store; handler.server.cli_exercises = self.cli
        handler.server.task_audit = self.audit
        handler.do_POST() if method == 'POST' else handler.do_GET()
        return handler._send.call_args

    def test_student_reaches_only_generators_and_every_action_is_audited(self):
        self.store = Exercises(self.root/'exercises'); self.cli = CliExercises(self.root/'cli')
        self.audit = TaskAudit(self.root/'task-audit')
        self.sid, self.session = self.login_student()
        self.assertEqual(self.session['identity']['role'], 'student')
        page = self.request('/aufgaben')
        self.assertEqual(page.args[0], 200); self.assertIn(b'data-role="student"', page.args[1])
        self.assertIn('Schüler'.encode(), page.args[1])
        home = self.request('/')
        self.assertEqual(home.args[0], 200); self.assertIn(b'id="student-home"', home.args[1])
        for path in ('/wartung', '/anmeldungen', '/sammelaktionen', '/unterricht'):
            result = self.request(path)
            self.assertEqual(result.args[0], 303)
            self.assertIn(('Location', '/cisco-lab/'), result.kwargs['headers'])
        for path in ('/api/state', '/api/activity', '/api/classroom', '/api/presence', '/api/logins',
                     '/api/task-log', '/api/backup', '/api/pilot', '/api/software'):
            self.assertEqual(self.request(path).args[0], 403, path)
        for path in ('/api/refresh', '/api/room/start', '/api/software/catalog'):
            self.assertEqual(self.request(path, 'POST', {}).args[0], 403, path)
        self.assertEqual(self.request('/app.js').args[0], 200)
        self.assertEqual(self.request('/api/session').args[0], 200)
        teacher_task = self.store.create({}, DEFAULT_TEXT, TEACHER)
        state = json.loads(self.request('/api/exercises').args[1])
        self.assertEqual(state['runs'], [])
        self.assertEqual(self.request('/api/exercises/task/'+teacher_task['id']).args[0], 404)
        self.assertEqual(self.request('/api/exercises/export/zip/'+teacher_task['id']+'/1').args[0], 404)
        created = self.request('/api/exercises/preview', 'POST', dict(settings={}, text=DEFAULT_TEXT, team_names=['Anna / Ben']))
        self.assertEqual(created.args[0], 200)
        task = json.loads(created.args[1])
        self.assertIn('configs', task['variants'][0])
        for path, body in (('/api/exercises/save', dict(identity=task['id'], text=DEFAULT_TEXT)),
                           ('/api/exercises/create', dict(settings={}, text=DEFAULT_TEXT)),
                           ('/api/exercises/template', dict(identity='', settings={}, text=DEFAULT_TEXT)),
                           ('/api/exercises/delete-all', dict(token='', confirmed=True))):
            self.assertEqual(self.request(path, 'POST', body).args[0], 403, path)
        revised = self.request('/api/exercises/text', 'POST', dict(identity=task['id'], text=DEFAULT_TEXT))
        self.assertEqual(revised.args[0], 200)
        for kind in ('pdf', 'zip'):
            export = self.request('/api/exercises/export/'+kind+'/'+task['id']+'/1')
            self.assertEqual(export.args[0], 200, kind)
        linux = self.request('/api/cli-exercises/preview', 'POST', dict(settings={}, text=CLI_TEXT))
        self.assertEqual(linux.args[0], 200)
        self.assertEqual(len(list((self.store.directory/'tasks').glob('*.json'))), 1)
        rows = self.audit.records()
        self.assertEqual([(row['generator'], row['action']) for row in rows],
                         [('linux', 'preview'), ('network', 'export_zip'), ('network', 'export_pdf'), ('network', 'preview')])
        self.assertTrue(all(row['account'] == 'schueler.test' and row['role'] == 'student' for row in rows))
        self.assertIn('Anna / Ben', rows[-1]['detail'])

    def test_admin_reads_task_log_and_collegium_does_not(self):
        self.store = Exercises(self.root/'exercises'); self.cli = CliExercises(self.root/'cli')
        self.audit = TaskAudit(self.root/'task-audit')
        self.audit.append('network', 'preview', 'a'*32, 'Fixture', STUDENT)
        for role, status in (('reader', 403), ('admin', 200)):
            self.sid, self.session = auth_fixtures.SessionAndRouteTests.login(self, role)
            result = self.request('/api/task-log')
            self.assertEqual(result.args[0], status, role)
        self.assertEqual(json.loads(result.args[1])['records'][0]['name'], 'Testschülerin')
        self.assertEqual(self.request('/api/task-log/page/1').args[0], 200)


if __name__ == '__main__':
    unittest.main()
