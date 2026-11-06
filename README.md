# CISCO-LAB mit Debian und Ansible

**Deutsch** · [English](README.en.md)

Ansible und eine lokale Webverwaltung für den Linux-Netzwerkunterricht an der BBS Einbeck. Im Raum arbeiten 16 Schüler-PCs in acht Zweierteams. Auf Debian 13 mit KDE Plasma üben die Schüler Switching, Routing und Fehlersuche an echten Cisco-Geräten.

[Projektseite und Screenshots](https://mail896.github.io/cisco-lab-ansible/) · [Betriebsanleitung](docs/dashboard.md) · [Paketwartung](docs/wartung-vorschau.md)

![KDE-Desktop von LAB-08B mit der Unterrichtsuhr](docs/media/lab-08b-desktop.jpg)

## Die eigene LAB-Webverwaltung

Für dieses LAB fiel die Wahl bewusst auf eine eigene Oberfläche anstelle von Semaphore. Sie bildet den Ablauf im Unterricht ab. Lehrkräfte wählen geprüfte Aufgaben und konkrete Zielrechner, testen auf LAB-08A oder LAB-08B und geben genau den geprüften Stand für den Raum frei.

![CISCO-LAB · Pilotdeploys](docs/media/dashboard-pilots.jpg)

[Übersicht](docs/media/dashboard-overview.jpg) · [Pilotdeploys](docs/media/dashboard-pilots.jpg) · [Desktopbilder](docs/media/dashboard-images.jpg)

## So arbeitet das LAB

Die interne Netzwerkkarte hält die Verbindung zum IServ-Schulnetz und die Standardroute. Die USB-Netzwerkkarte führt ins Übungsnetz. Schüler richten dort manuelle IPv4- und IPv6-Adressen sowie gezielte Routen über den Labornetz-Dialog ein. Ein Dienst prüft die Eingaben und wendet sie ausschließlich auf den zugeordneten USB-Adapter an. Wireshark schneidet dort mit. PuTTY und Minicom koordinieren den Zugriff auf das serielle Konsolenkabel.

Auf LAB-Teacher können Lehrkräfte Ansible-Aufgaben prüfen, auf einem Pilotrechner ausführen und nach Sichttest für den Raum freigeben. Die Webverwaltung bietet außerdem Desktopbilder, Sammelaktionen und verschlüsselte Sicherungen. Das lokale Sammelkonto wird unabhängig von IServ eingerichtet und hat keinen SSH-Zugang.

[Netzplan](docs/labornetz.svg) · [Live-Screenshot des Labornetz-Dialogs](docs/media/lab-08b-network.jpg)

## Dateien im Repository

| Pfad | Zweck |
| --- | --- |
| `dashboard/` | Webverwaltung, Pilotabnahme, Sammelaktionen und Sicherung |
| `roles/`, `block*.yml` | KDE, Firefox, serielle Konsole und Netzwerkkonfiguration |
| `network/` | Labornetz-Dialog, Vermittlungsdienst und USB-Mitschnitt |
| `bootstrap-client.yml`, `fresh-client.yml` | SSH-Erstaufnahme und Baseline für ein frisches IServ-Image |
| `local-cisco.yml` | Lokales Sammelkonto und dessen Desktopvorgaben |
| `maintenance/` | APT-Vorschau, Wartungspilot und systemd-Timer |
| `inventory.example.ini` | Beispielinventar zur Anpassung |
| `files/README.md` | Lokal bereitzustellende Bilder und Pakete |

## Beispiel lokal prüfen

```bash
ansible-playbook -i inventory.example.ini --syntax-check fresh-client.yml
ansible-playbook -i inventory.example.ini --limit LAB-08B --list-tasks fresh-client.yml
```

Für den Betrieb ein LAB-Inventar verwenden und SSH-Fingerabdruck sowie interne Netzwerkkarte prüfen. Änderungen zuerst auf einem einzelnen Pilotclient testen. Laufzeit-Zugangsdaten, Schlüssel, Geräte-Manifeste, verschlüsselte Sicherungen, Herstellerpakete und das versionsgebundene Winbind-Modul werden lokal bereitgestellt. Die Playbooks verwalten LAB-Teacher und die LAB-Clients. Der zentrale IServ-Server wird separat verwaltet.

Bei veröffentlichten Projektupdates beide Sprachfassungen und bei sichtbaren Änderungen die betroffenen Screenshots gemeinsam aktualisieren.
