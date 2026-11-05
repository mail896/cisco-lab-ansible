# Vorschau für die LAB-Wartung

Die Vorschau läuft auf LAB-Teacher. Sie verbindet sich mit den 16 Schülerclients
über ihre bereits geprüften SSH-Hostschlüssel. APT wird im Simulationsmodus
mit den vorhandenen Paketlisten abgefragt.

```bash
python3 -B -m maintenance.preview --lab08b-known-hosts "$lab08b_known_hosts"
```

- Rechner mit Anmeldung oder noch eingebundenem IServ-Home werden für
  Download-only vorgemerkt. Die eigene SSH-Prüfsitzung zählt dabei nicht.
- Ist ein Rechner per SSH nicht erreichbar, kann er ein WOL-Kandidat sein.
  Vor einem späteren Herunterfahren muss nachweisbar sein, dass dieser
  Wartungslauf ihn eigens geweckt hat. Die Vorschau setzt dafür keine Freigabe.
- Gefährdet `autoremove` LAB-Software, den laufenden Kernel oder den letzten
  startfähigen Ersatzkernel, wird die gesamte Entfernung zur Prüfung markiert.
  Als startfähig berücksichtigt die Vorschau installierte Kernel mit
  vorhandener Kernel- und Initramfs-Datei.
- Ein Upgrade der Samba-Pakete, von denen der derzeitige Winbind-Patch abhängt,
  wird vor einer Installation zur Prüfung markiert. Ein passender Nachfolger
  muss gegen den tatsächlichen Paketstand getestet werden.
- Das Startfenster gilt montags bis freitags von 18:00 Uhr bis vor 20:00 Uhr
  in Europe/Berlin. Der Kalender wurde lokal mit systemd geprüft.
  Ein bereits begonnener Lauf darf fertig werden.
- Die Vorschau liest die bisherigen APT-Automatik-Einstellungen. Paket-Downloads,
  Installationen, Neustarts, WOL, Aufwecken von LAB-Teacher und Änderungen an
  Timern sind kein Teil dieses Kommandos.

## Nächster Implementierungsschritt

Die Ausführung zuerst auf LAB-08B prüfen. Sitzungen vor Paketinstallation,
Neustart und Herunterfahren erneut abfragen. Autoremove nach der Installation
neu simulieren und nur bei weiterhin erlaubtem Ergebnis starten. Bei Fehlern
den Client eingeschaltet lassen und die übrigen weiter warten. LAB-Teacher
kommt nach den Clients. Die vorhandene APT-Automatik vor einer späteren
Aktivierung mit dem gemeinsamen Ablauf abstimmen.

LAB-Teacher meldet einen RTC-Wecker mit aktivierter Weckquelle. Aufwachen
aus Standby und Einschalten nach vollständigem Ausschalten werden getrennt
praktisch geprüft.
