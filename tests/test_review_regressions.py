"""Reproduce review findings without accessing devices or live application state."""
import threading
import base64
from http.server import ThreadingHTTPServer
from pathlib import Path
import socket
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from dashboard.playbook_groups import PlaybookGroups
from dashboard.software import Software
from dashboard.own_packages import OwnPackages
from dashboard.server import Handler


class JobFinalizationTests(unittest.TestCase):
    def test_software_releases_operation_lock_when_final_save_fails(self):
        manager=object.__new__(Software)
        manager.deployments=SimpleNamespace(lock=threading.Lock())
        manager.guard=threading.RLock()
        manager.save=Mock(side_effect=[None,None,OSError('disk full')])
        manager.on_host=Mock(return_value={'host':'LAB-08A','digest':'a'*64})
        manager.deployments.lock.acquire()
        run=dict(kind='plan',status='running',targets=['LAB-08A'],active=[],results={},plans={})
        with self.assertRaisesRegex(OSError,'disk full'):
            manager.execute(run)
        self.assertFalse(manager.deployments.lock.locked())
        self.assertEqual(run['status'],'succeeded')


    def test_group_releases_operation_lock_when_final_save_fails(self):
        manager=object.__new__(PlaybookGroups)
        manager.deployments=SimpleNamespace(lock=threading.Lock())
        manager.software=SimpleNamespace(invalidate_inventory=Mock())
        manager.guard=threading.RLock()
        manager.save_run=Mock(side_effect=[None,OSError('disk full')])
        manager.on_host=Mock(return_value={'status':'succeeded'})
        manager.deployments.lock.acquire()
        run=dict(status='running',targets=['LAB-08A'],results={},active={})
        with self.assertRaisesRegex(OSError,'disk full'):
            manager.execute(run)
        self.assertFalse(manager.deployments.lock.locked())

class RequestTimeoutTests(unittest.TestCase):
    def test_stalled_authenticated_package_upload_releases_lock_and_removes_partial_file(self):
        class FastTimeoutHandler(Handler):
            timeout=0.1
            def log_message(self,*args):
                pass
        self.assertEqual(Handler.timeout,30)
        with TemporaryDirectory() as temporary:
            root=Path(temporary)
            password=root/'password';password.write_text('fixture');password.chmod(0o600)
            lock=threading.Lock()
            with ThreadingHTTPServer(('127.0.0.1',0),FastTimeoutHandler) as server:
                server.base_path='/cisco-lab';server.https_host=None
                server.password_path=password;server.auth=None
                server.deployments=SimpleNamespace(lock=lock)
                server.software=SimpleNamespace(own=OwnPackages(root/'packages'))
                thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
                try:
                    with socket.create_connection(server.server_address,timeout=2) as connection:
                        auth=base64.b64encode(b'lab-admin:fixture').decode()
                        request=('POST /cisco-lab/api/software/package/upload HTTP/1.0\r\n'
                            f'Host: 127.0.0.1:{server.server_port}\r\nOrigin: http://127.0.0.1:{server.server_port}\r\n'
                            f'Authorization: Basic {auth}\r\nContent-Type: application/octet-stream\r\n'
                            'X-LAB-Package-Name: fixture.deb\r\nContent-Length: 100\r\n\r\nx')
                        connection.sendall(request.encode())
                        response=connection.recv(4096)
                        self.assertIn(b'503',response.splitlines()[0])
                    self.assertFalse(lock.locked())
                    self.assertEqual(list((root/'packages').iterdir()),[])
                finally:
                    server.shutdown();thread.join(timeout=2)

