"""Real offscreen Qt dialog -> Unix socket -> broker -> simulated NM."""
import os
os.environ['QT_QPA_PLATFORM']='offscreen'
import tempfile
from pathlib import Path
import time
import threading
import unittest
from unittest.mock import patch
try:
    from PyQt5.QtWidgets import QApplication,QMessageBox
    from network.dialog.main import Dialog
    QT_AVAILABLE=True
except ImportError:
    QT_AVAILABLE=False
from network.broker import auth
from network.broker.client import request as wire_request
from network.broker.service import Server
from tests.test_network_broker import BackendFixture,APPLY


@unittest.skipUnless(QT_AVAILABLE and os.geteuid()==0,'Requires test-only Qt wheels and root-owned fake server')
class GuiTests(BackendFixture):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])
    def setUp(self):
        super().setUp()
        self.server=Server(self.root/'gui.sock',self.backend,555,
            authenticate=lambda conn:(auth.Peer(123,1001,555,frozenset([555])),{'id':'gui','uid':1001,'name':'student'}),
            boot_id='gui-boot',housekeeping=False)
        self.thread=threading.Thread(target=self.server.serve_forever,kwargs={'poll_interval':0.01},daemon=True);self.thread.start()
        self.patch=patch('network.dialog.main.request',lambda payload:wire_request(payload,str(self.root/'gui.sock')));self.patch.start()
        self.warning=patch('network.dialog.main.QMessageBox.warning');self.mock_warning=self.warning.start()
        self.dialog=Dialog();self.dialog.show();self.wait()
    def tearDown(self):
        self.wait();self.dialog.close();self.dialog.deleteLater();self.app.processEvents()
        self.warning.stop();self.patch.stop();self.server.shutdown();self.thread.join();self.server.server_close()
        super().tearDown()
    def wait(self):
        deadline=time.monotonic()+3
        while time.monotonic()<deadline:
            self.app.processEvents()
            if self.dialog.worker is None or not self.dialog.worker.isRunning():
                self.app.processEvents();return
            time.sleep(0.005)
        self.fail('GUI worker did not finish')
    def test_status_apply_reload_and_reset(self):
        self.dialog.addresses.setPlainText('\n'.join(APPLY['addresses']))
        self.dialog.routes.setPlainText('172.16.0.0/29 via 198.51.100.1')
        self.dialog.apply.click();self.wait()
        self.assertEqual(self.backend.status()['configuration'],APPLY)
        self.dialog.reload.click();self.wait()
        self.assertIn('198.51.100.6/29',self.dialog.addresses.toPlainText())
        with patch('network.dialog.main.QMessageBox.question',return_value=QMessageBox.Yes):self.dialog.clear.click()
        self.wait();self.assertEqual(self.dialog.addresses.toPlainText(),'')
        self.assertEqual(self.backend.status()['configuration']['action'],'clear')
        self.mock_warning.assert_not_called()
    def test_invalid_school_address_preserves_form_and_profile(self):
        before=self.lab.read_bytes()
        self.dialog.addresses.setPlainText('203.0.113.99/24')
        self.dialog.apply.click();self.wait()
        self.mock_warning.assert_called_once()
        self.assertEqual(self.dialog.addresses.toPlainText(),'203.0.113.99/24')
        self.assertEqual(before,self.lab.read_bytes())
    def test_malformed_route_never_reaches_backend(self):
        self.nm.calls=[]
        self.dialog.routes.setPlainText('nmcli connection delete LAN-Schule')
        self.dialog.apply.click();self.app.processEvents()
        self.mock_warning.assert_called_once();self.assertEqual(self.nm.calls,[])

if __name__=='__main__':unittest.main()
