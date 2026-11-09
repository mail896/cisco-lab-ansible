"""The APP dashboard must stay inside its assigned URL prefix."""
from http.server import BaseHTTPRequestHandler
from email.message import Message
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from dashboard.provision import ENTRIES
from dashboard.server import Handler
from dashboard.server import PUBLIC_THEME_ASSETS, THEME_SCRIPT, THEME_STYLESHEET


class PrefixTests(unittest.TestCase):
    def handler(self, path, base='/cisco-lab', profile='teacher'):
        handler = Handler.__new__(Handler)
        handler.path = path
        handler.server = SimpleNamespace(base_path=base, backups=SimpleNamespace(profile=profile))
        handler._trusted_request = Mock(return_value=True)
        handler._authorized = Mock(return_value=True)
        handler._json = Mock()
        handler._send = Mock()
        return handler

    def test_only_configured_prefix_is_accepted(self):
        for path in ('/api/state', '/other/api/state', '/cisco-lab-other/api/state',
                     '/cisco-lab/api/state?x=1'):
            handler = self.handler(path)
            handler.do_GET()
            handler._json.assert_called_once()
            self.assertEqual(handler._json.call_args.args[0], 404)
            handler._authorized.assert_not_called()

    def test_html_links_and_assets_remain_under_prefix(self):
        handler = self.handler('/cisco-lab/schuelerclients')
        handler.do_GET()
        status, body, kind = handler._send.call_args.args
        self.assertEqual(status, 200)
        self.assertIn(b'data-base-path="/cisco-lab"', body)
        self.assertIn(b'href="/cisco-lab/schuelerclients"', body)
        self.assertIn(b'src="/cisco-lab/app.js"', body)
        self.assertNotIn(b'__BASE_PATH__', body)
        self.assertIn('text/html', kind)
        self.assertIn(('/cisco-lab'+THEME_SCRIPT).encode(),body)
        self.assertIn(('/cisco-lab'+THEME_STYLESHEET).encode(),body)
        self.assertNotIn(b'__THEME_',body)
        self.assertLess(body.index(THEME_SCRIPT.encode()),body.index(b'/app.css'))

    def test_content_addressed_theme_assets_are_public_cacheable_and_fixed(self):
        for path,(name,kind) in PUBLIC_THEME_ASSETS.items():
            handler=self.handler('/cisco-lab'+path);handler._authorized.return_value=False
            handler.do_GET();handler._authorized.assert_not_called()
            self.assertEqual(handler._send.call_args.args[0],200)
            self.assertEqual(handler._send.call_args.args[2],kind)
            self.assertEqual(handler._send.call_args.kwargs['cache_control'],'public, max-age=86400')
        handler=self.handler('/cisco-lab/theme.not-a-real-hash.js');handler._authorized.return_value=False
        handler.do_GET();handler._authorized.assert_called_once();handler._send.assert_not_called()

    def test_legacy_teacher_root_still_works_without_prefix(self):
        handler = self.handler('/', base='')
        handler.do_GET()
        self.assertIn(b'href="/schuelerclients"', handler._send.call_args.args[1])

    def test_apps_html_has_independent_backup_targets_without_repeated_key_confirmation(self):
        handler = self.handler('/cisco-lab/', profile='apps')
        handler.do_GET()
        body = handler._send.call_args.args[1]
        for target in ('apps','mac','download'):
            self.assertIn(('data-backup-target="'+target+'"').encode(),body)
        self.assertNotIn(b'id="backup-confirm"',body)
        self.assertNotIn(b'__CONTROLLER_LABEL__', body)
        self.assertNotIn(b'__BACKUP_DESCRIPTION__', body)

    def test_https_origin_requires_exact_local_proxy_headers(self):
        handler = self.handler('/cisco-lab/')
        handler.server.server_port = 8765
        handler.server.https_host = 'lab-app.example.org'
        handler.client_address = ('127.0.0.1', 53000)
        handler.headers = Message()
        handler.headers['Host'] = '127.0.0.1:8765'
        handler.headers['X-Forwarded-Proto'] = 'https'
        handler.headers['X-Forwarded-Host'] = 'lab-app.example.org'
        self.assertEqual(handler._request_origin(), 'https://lab-app.example.org')
        handler.headers['X-Forwarded-Host'] = 'other.example'
        self.assertIsNone(handler._request_origin())
        del handler.headers['X-Forwarded-Host']
        handler.client_address = ('203.0.113.55', 53000)
        self.assertIsNone(handler._request_origin())

    def test_teacher_installer_requires_existing_authentication(self):
        handler = self.handler('/cisco-lab/api/teacher/setup', profile='apps')
        handler._authorized.return_value = False
        with patch('dashboard.server.teacher_installer') as download:
            handler.do_GET()
        download.assert_not_called()

    def test_teacher_cleanup_download_is_authenticated_admin_only_and_attachment(self):
        endpoint = '/cisco-lab/api/teacher/cleanup'
        handler = self.handler(endpoint, profile='apps')
        handler._authorized.return_value = False
        with patch('dashboard.server.teacher_script') as download:
            handler.do_GET()
        download.assert_not_called()
        handler = self.handler(endpoint, profile='apps')
        handler.identity = {'role': 'reader'}
        with patch('dashboard.server.teacher_script') as download:
            handler.do_GET()
        download.assert_not_called()
        self.assertEqual(handler._json.call_args.args[0], 403)
        handler = self.handler(endpoint, profile='apps')
        with patch('dashboard.server.teacher_script', return_value=b'reviewed cleanup'):
            handler.do_GET()
        self.assertEqual(handler._send.call_args.args[:2], (200, b'reviewed cleanup'))
        self.assertIn(('Content-Disposition', 'attachment; filename="cisco-lab-teacher-cleanup.py"'), handler._send.call_args.kwargs['headers'])

    def test_only_fixed_public_icons_bypass_authentication_and_allow_caching(self):
        for path, kind in (('/assets/cisco-lab.svg', 'image/svg+xml'),
                           ('/assets/cisco-lab-icon.png', 'image/png')):
            handler = self.handler('/cisco-lab' + path)
            handler._authorized.return_value = False
            handler.do_GET()
            handler._authorized.assert_not_called()
            self.assertEqual(handler._send.call_args.args[0], 200)
            self.assertEqual(handler._send.call_args.args[2], kind)
            self.assertEqual(handler._send.call_args.kwargs['cache_control'], 'public, max-age=86400')
        for path in ('/', '/api/state', '/assets/../index.html', '/assets/other.png'):
            handler = self.handler('/cisco-lab' + path)
            handler._authorized.return_value = False
            handler.do_GET()
            handler._authorized.assert_called_once()
            handler._send.assert_not_called()

    def test_teacher_installer_is_served_only_under_authenticated_app_prefix(self):
        handler = self.handler('/cisco-lab/api/teacher/setup', profile='apps')
        with patch('dashboard.server.teacher_installer', return_value=b'reviewed installer'):
            handler.do_GET()
        self.assertEqual(handler._send.call_args.args[:2], (200, b'reviewed installer'))
        handler = self.handler('/cisco-lab/api/teacher/setup')
        with patch('dashboard.server.teacher_installer') as download:
            handler.do_GET()
        download.assert_not_called()
        self.assertEqual(handler._json.call_args.args[0], 404)

    def test_all_catalogue_sources_are_read_from_the_actual_project_files(self):
        root = Path(__file__).resolve().parents[1]
        for key, entry in ENTRIES.items():
            with self.subTest(playbook=key):
                handler = self.handler('/cisco-lab/api/playbooks/' + key, profile='apps')
                handler.do_GET()
                handler._json.assert_called_once_with(200, {
                    'entry': entry,
                    'content': (root / entry['source']).read_text(encoding='utf-8'),
                })
                handler._send.assert_not_called()

    def test_catalogue_source_requires_authentication(self):
        handler = self.handler('/cisco-lab/api/playbooks/bootstrap', profile='apps')
        handler._authorized.return_value = False
        with patch('dashboard.server.source') as read_source:
            handler.do_GET()
        read_source.assert_not_called()
        handler._send.assert_not_called()

    def test_unknown_or_missing_catalogue_source_has_a_json_error(self):
        handler = self.handler('/cisco-lab/api/playbooks/not-a-playbook', profile='apps')
        handler.do_GET()
        handler._json.assert_called_once_with(404, {'error': 'Playbook nicht gefunden.'})
        handler = self.handler('/cisco-lab/api/playbooks/bootstrap', profile='apps')
        with patch('dashboard.server.source', side_effect=FileNotFoundError('private path')):
            handler.do_GET()
        handler._json.assert_called_once_with(404, {'error': 'Playbook nicht gefunden.'})

    def test_unreadable_catalogue_source_has_a_safe_json_error(self):
        for error in (PermissionError('private path'), OSError('private path'),
                      UnicodeDecodeError('utf-8', b'\xff', 0, 1, 'invalid')):
            with self.subTest(error=type(error).__name__):
                handler = self.handler('/cisco-lab/api/playbooks/bootstrap', profile='apps')
                with patch('dashboard.server.source', side_effect=error):
                    handler.do_GET()
                handler._json.assert_called_once_with(503, {
                    'error': 'Playbook-Quelltext konnte nicht gelesen werden. Bitte erneut versuchen.',
                })


if __name__ == '__main__':
    unittest.main()
