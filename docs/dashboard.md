# Lokale LAB-Verwaltung

**Deutsch** · [English](dashboard.en.md)

Stand: 07.11.2026. Der lokale [Projektstatus](projektstatus.txt) unterscheidet
fertige Funktionen, offene technische Arbeiten und ausstehende Praxistests.

Die Verwaltung läuft auf LAB-Teacher als systemd-Benutzerdienst von `marc`.
Der HTTPS-Zugang führt über Caddy zu nginx und dem nur auf Loopback lauschenden
Python-Dienst. Der Web-Benutzer heißt `lab-admin`. Das Passwort steht lokal in
`~/.local/state/cisco-lab-dashboard/passwort`; Änderungen gelten ab der nächsten
Anfrage. Zugangsdaten gehören nicht ins Git. Die GUI verändert den zentralen
IServ-Server nicht.

## Pilot und Raumverteilung

1. Unter **Pilotdeploys** LAB-08A/B und den Auftrag wählen. Aufgabenliste prüfen.
2. Bei Desktop-, Sitzungs- und Paketänderungen eine freie Testsitzung verwenden.
   Belegte Clients, aktive IServ-Einbindungen und laufende APT-Wartung werden
   vor den Änderungen geprüft. Eine ausdrücklich gewählte reguläre Abmeldung
   muss vollständig abgeschlossen sein, bevor die Installation beginnt.
3. Den Pilot starten und dessen Laufprotokoll öffnen. Der vollständige
   Ansible-Text bleibt gespeichert; die Browseransicht zeigt dessen letzten Teil.
4. Die vorgesehenen Prüfpunkte am Client testen und den aktuellen erfolgreichen
   Pilotlauf abnehmen. Ältere Läufe bleiben im Verlauf, ersetzen diese Abnahme
   aber nicht. Geänderte Quellen benötigen einen neuen Pilotlauf.
5. Den abgenommenen Stand unter **Playbooks** beziehungsweise **Desktopbilder**
   auf den Raum verteilen. Ergebnisse sind je Client sichtbar; ausstehende
   Geräte können mit demselben freigegebenen Stand nachgeholt werden.

YAML wird separat bearbeitet. Formulare und Auftragskatalog geben ausschließlich
die vorgesehenen Verwaltungsaktionen frei. GUI und zentrale Wartungssteuerung
teilen eine prozessübergreifende Sperre gegen konkurrierende Verwaltungsaufträge.

## Frisches IServ-Image aufnehmen

1. Der Betreiber installiert das Debian-13-KDE-Image über IServ. Zielmodus für
   Schülerclients ist `tmpfs (non persistent)`. Die interne NIC benötigt die
   Schuladresse und die Default-Route. Während der Imageinstallation kann es
   nötig sein, den USB-Laboradapter vorübergehend abzuziehen.
2. Den neuen ED25519-Fingerabdruck unabhängig am Client prüfen. Ein geänderter
   Hostschlüssel wird nicht automatisch vertraut. Den vorbereiteten privaten
   Hardwareeintrag mit interner NIC, USB-NIC, Profil-UUIDs und MACs abgleichen.
3. Unter **Neuinstallation** die SSH-Erstaufnahme mit dem bekannten lokalen
   Administratorpasswort starten. Die Passwörter werden nicht in Laufprotokollen
   oder JSON-Dateien gespeichert. Danach wird mit Schlüssel und sudo geprüft.
4. Die Basisinstallation für die vorbereiteten Ziele starten. Die GUI kann zwei
   Geräte parallel bearbeiten und weitere einreihen. Stoppen hält nach einer
   abgeschlossenen Phase an; es beendet keine laufende Paketinstallation.
5. Nach Abschluss lokale `cisco`-Anmeldung, IServ-Anmeldung, Files, Hintergrund,
   symbolfreie Arbeitsfläche, Labornetz, USB-Konsole und Wireshark prüfen.

Die gemeinsame Folge `lab-baseline.yml` enthält drei Komponenten:

| Phase | Inhalt |
| --- | --- |
| Basis | lokale PAM-Abgrenzung, Winbind-Fix, Netzwerk, WOL, Konsole, Wireshark, Packet Tracer, KDE, U-Zeiten, Firefox, optional Veyon |
| Sammelkonto | lokales `cisco`, LAB-Gruppenrechte, Desktopvorgaben, kein SSH-Zugang |
| Wartung | neuer Abendablauf, APT-Abstimmung, Timer und Registrierung für die zentrale Steuerung |

Die GUI kann nach einem Fehler an einer Phasengrenze fortsetzen. Eine erneut
aufgenommene Wartungsphase wiederholt nicht die bereits erfolgreiche Basis und
Kontoeinrichtung. Die CLI verwendet dieselben Komponenten. Der vollständige Lauf
auf LAB-08A bestand mit 206 erfolgreichen Aufgaben. Ein neuer GUI-Komplettlauf
nach den letzten Änderungen auf einem weiteren frisch installierten Image steht
noch aus. Lokale Warteschlangen- und Wiederanlauftests sind bestanden.

## Winbind und lokale Konten

Der versionsgebundene Reconnect-Fix ist verpflichtender Teil der Baseline.
Der Installer verlangt die bekannte Samba-Version und ein bekanntes Original-
oder Patch-Modul. Er sichert das Original, installiert atomar und prüft
Benutzerauflösung, LAB-Mitgliedschaft, Rechnervertrauen und das geladene Modul.
Bei einer fehlgeschlagenen Prüfung stellt er das Original wieder her.
Unbekannte Versionen werden nicht überschrieben. Die private Binärdatei und
Kontovorgaben bleiben außerhalb des öffentlichen Git.

Lokale `/etc/passwd`-Konten überspringen ausschließlich den IServ-Home-Mount
in PAM. Echte IServ-Konten benutzen weiterhin die vorhandene IServ-Anbindung.
Das verhindert falsche RAM-Homes für den lokalen Verwaltungsadministrator.

## Abendwartung

Mo–Fr gilt das Startfenster 18–20 Uhr, Europe/Berlin. Begonnene Installationen
dürfen fertig werden. Im neuen Modus werden Nutzer zuerst regulär abgemeldet;
Sitzungsende und verbliebene RAM-Homes werden geprüft. Erst danach folgen
Installation, geschützte Paketbereinigung und nach Erfolg immer ein Neustart.
Fehler beenden den Lauf und lassen den Client eingeschaltet. Bei Archivmodus
werden unklare verbliebene Home-Mounts zur Prüfung erhalten.

Der neue Modus ist auf LAB-08A aktiv. LAB-08B behält als Vergleich den früheren
Pilot mit Download bei belegter Sitzung. Die weiteren Clients werden nach ihrer
schrittweisen Neuinstallation aufgenommen. Der normale Logout erzeugt derzeit
nicht nachweislich jedes Mal ein leeres RAM-Home; der Neustart entfernt es.
Files und Groups liegen auf IServ und bleiben erhalten. Ein Profil-Löschauftrag
ist auf Nutzerwunsch entfallen.

Die zentrale Steuerung akzeptiert ausschließlich registrierte Clients mit
geprüfter Schul-MAC und gepinntem SSH-Schlüssel. Sie sendet bei Bedarf WOL,
verfolgt die Wartung und bestätigt den neuen Boot anhand der Boot-ID. Nur ein
nachweislich eigens geweckter, weiterhin unbelegter Client darf danach wieder
herunterfahren. Bereits erfolgreich gewartete Clients werden am selben Tag
nicht für einen weiteren Lauf geweckt. Ergebnisse erscheinen unter **Wartung**.
Der echte WOL-Pilot auf LAB-08A einschließlich abschließendem Ausschalten ist
bestanden. Der zentrale Teacher-Timer ist für diesen registrierten Client aktiv;
Teacher-Aufwachen und Betrieb ohne KDE-Anmeldung sind noch separat abzusichern.

## Sammelaktionen und Bilder

Sammelaktionen bieten Aufwecken per Magic Packet, reguläres Schüler-Abmelden,
Neustart/Herunterfahren freier Geräte sowie die einmalige Anmeldung des lokalen
Sammelkontos `cisco`. Sie verwenden keine Veyon-Fernsteuerung. Power-Aktionen
prüfen vor der verzögerten Ausführung erneut Sitzung, Mounts und Wartung.

Die Bildauswahl liest PNG/JPEG aus `~/Bilder/CISCO-LAB`. Auswahl speichern
verteilt noch nichts. Erst Pilot, Abnahme und Raumverteilung installieren den
Stand. Desktop- und Sperrbild sind getrennt; das Sperrbild gilt auch für SDDM.
Bei einem freien Client kann der Greeter unmittelbar neu geladen werden.
Der Desktop übernimmt das Bild beim nächsten Login. Unveränderte Bildstände
überspringen Übertragung und erneute Sicherung.

## Verschlüsselte Sicherung

Neue Sicherungen enthalten Projektstand, private Konfiguration, SSH-Material,
Bildbibliothek, Benutzerdienste und Dashboard-Zustand einschließlich Zugang und
Laufhistorie. Sie liegen verschlüsselt auf `data/backup_data`; optional werden
Archiv und SHA-256-Datei per SCP auf das festgelegte macOS-Ziel übertragen.
Die SCP-Passwortdatei und der Wiederherstellungsschlüssel sind ausgeschlossen.
Der Schlüssel ist nach Bestätigung des Betreibers getrennt außerhalb von
LAB-Teacher sicher verwahrt.

Die Sicherung vom 07.11. 15:59 enthält auch den Root-Systemexport. Isoliert
wiederhergestellt wurden 386 Dateien und 12 eingebettete Systemdateien. Der
SSH-Schlüssel passt, TLS- und Veyon-Schlüssel stimmen samt Eigentümer und Modus
mit dem Live-Export überein; das wiederhergestellte Dashboard liefert HTTP 200.
Das ist ein Konfigurations-Restore, noch kein vollständiger Ersatzrechner.
Ältere Sicherungen vom 06.11. enthalten nicht den vollständigen Dashboard-Zustand.

Rootgeschützte TLS-/Veyon-Dateien benötigen den festen Exporter
`maintenance/teacher-backup-system.py` mit einer begrenzten sudo-Regel ohne
Argumente. Die Teacher-Einrichtung wurde vom Administrator ausgeführt und geprüft. Der Sicherungseintrag zeigt, ob dieser Systemexport enthalten
ist. Der Dashboard-Benutzerdienst muss den vorgesehenen sudo-Aufruf zulassen.

Die Rotation behält 90 Tage und zusätzlich die zuletzt als funktionierend
bestätigte Sicherung. Ohne Bestätigung bleibt die neueste erfolgreiche erhalten.
Die Bestätigung setzt eine passende Prüfsumme voraus. Es werden ausschließlich
bekannte Archivpaare gelöscht. Bei externer Löschstörung bleibt lokal erhalten.
GitHub veröffentlicht Quellen und Dokumentation und ersetzt diese Sicherung nicht.


## Einschalten des Lehrer-PCs nach fehlgeschlagenem Standby-Test

Der praktische Standby-Aufwachtest am 07.11.2026 schlug fehl. Auch WOL holte
den Teacher nicht zurück; eine Kollegin musste ihn vor Ort hart neu starten.
Die konkrete Kernel-/Firmwareursache ist nicht belegt. Der zuvor vorbereitete
Standby-Weg wird nicht weiter verwendet; Test und Installer verweigern neue Läufe.
`maintenance/retire-teacher-suspend.sh` deaktiviert nach Root-Ausführung den alten
Timer und maskiert Schlaf-/Ruhezustandsziele. Es löst selbst keinen Neustart aus.
Die Root-Ausführung dieser Bereinigung steht noch aus.

Der Betreiber bestätigt seit Langem funktionierendes WOL aus vollständig
 ausgeschaltetem Zustand. Dieser Startweg benötigt einen externen WOL-Auslöser;
ein lokaler WakeSystem-Timer kann den ausgeschalteten Rechner nicht einschalten.
Caddy liegt in einem anderen gerouteten Netz und ist noch kein nachgewiesener
WOL-Sender für den Schulanschluss. Auf IServ werden keine Zeitpläne oder Skripte
angelegt. Linger, Dashboard und Client-Wartungssteuerung laufen nach dem Neustart.
Teacher-eigene Updates bleiben ein separater offener Arbeitsblock.
