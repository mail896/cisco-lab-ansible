#!/usr/bin/python3
"""KDE/PyQt5 laboratory network editor connected only to the local broker."""
if __package__ in (None,''):
    import sys
    from pathlib import Path
    sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
    __package__='network.dialog'

import sys
from PyQt5.QtCore import QThread, pyqtSignal
from PyQt5.QtWidgets import (QApplication,QDialog,QVBoxLayout,QLabel,QPlainTextEdit,
                            QPushButton,QHBoxLayout,QMessageBox)
from network.broker.client import request
from network.dialog.forms import to_request,from_configuration


class Worker(QThread):
    result=pyqtSignal(object)
    error=pyqtSignal(str)
    def __init__(self,payload):
        super().__init__();self.payload=payload
    def run(self):
        try:self.result.emit(request(self.payload))
        except Exception as error:self.error.emit(str(error))


class Dialog(QDialog):
    def __init__(self):
        super().__init__();self.worker=None
        self.setWindowTitle('Labornetz einstellen');self.resize(620,520)
        layout=QVBoxLayout(self)
        layout.addWidget(QLabel('USB-Labornetz – die IServ-Verbindung bleibt geschützt.'))
        self.status=QLabel('Status wird geladen …');self.status.setWordWrap(True);layout.addWidget(self.status)
        layout.addWidget(QLabel('Manuelle IPv4-/IPv6-Adressen mit Präfix, eine je Zeile:'))
        self.addresses=QPlainTextEdit();self.addresses.setPlaceholderText('198.51.100.6/29\n2001:db8:192::6/64');layout.addWidget(self.addresses)
        layout.addWidget(QLabel('Gezielte Routen: Zielnetz/Praefix via Routeradresse'))
        self.routes=QPlainTextEdit();self.routes.setPlaceholderText('172.16.0.0/29 via 198.51.100.1\n2001:db8:172::/64 via fe80::1');layout.addWidget(self.routes)
        layout.addWidget(QLabel('Kein DHCP. Die Default-Route bleibt im Schulnetz.'))
        row=QHBoxLayout();layout.addLayout(row)
        self.reload=QPushButton('Status neu laden');self.clear=QPushButton('Zurücksetzen');self.apply=QPushButton('Übernehmen')
        for button in (self.reload,self.clear,self.apply):row.addWidget(button)
        self.reload.clicked.connect(lambda:self.start({'action':'status'}))
        self.clear.clicked.connect(self.reset);self.apply.clicked.connect(self.save)
        self.start({'action':'status'})

    def start(self,payload):
        if self.worker is not None and self.worker.isRunning():return
        self.set_busy(True);self.status.setText('Labornetz wird geprüft …')
        self.worker=Worker(payload)
        self.worker.result.connect(self.done);self.worker.error.connect(self.failed)
        self.worker.finished.connect(lambda:self.set_busy(False))
        self.worker.start()

    def set_busy(self,value):
        for widget in (self.reload,self.clear,self.apply,self.addresses,self.routes):widget.setEnabled(not value)

    def done(self,response):
        configuration=response.get('configuration')
        if configuration is not None:
            addresses,routes=from_configuration(configuration)
            self.addresses.setPlainText(addresses);self.routes.setPlainText(routes)
        self.status.setText('Adapter: '+response.get('interface','USB-Labor')+'\n'+response.get('notice','Einstellungen übernommen.'))

    def failed(self,message):
        self.status.setText('Vorgang fehlgeschlagen. Eingaben bleiben erhalten.')
        QMessageBox.warning(self,'Labornetz',message)

    def save(self):
        try:payload=to_request(self.addresses.toPlainText(),self.routes.toPlainText())
        except ValueError as error:
            QMessageBox.warning(self,'Eingaben prüfen',str(error));return
        self.start(payload)

    def reset(self):
        if QMessageBox.question(self,'Labornetz zurücksetzen','Alle Übungsadressen und Laborrouten entfernen?')==QMessageBox.Yes:
            self.start({'action':'clear','addresses':[],'routes':[]})

    def closeEvent(self,event):
        if self.worker is not None and self.worker.isRunning():
            event.ignore();return  # never destroy a running thread or cancel a network transaction
        event.accept()


def main():
    app=QApplication(sys.argv);dialog=Dialog();dialog.show();return app.exec_()

if __name__=='__main__':sys.exit(main())
