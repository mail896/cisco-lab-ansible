# Lokale LAB-Verwaltung

**Deutsch** · [English](dashboard.en.md)

Die Webverwaltung läuft als Python-Dienst auf LAB-Teacher und verwendet dessen geprüften SSH-Zugang zu den LAB-Clients. Der HTTP-Dienst bindet an Loopback. Für Zugriff aus dem Schulnetz einen authentifizierten HTTPS-Reverse-Proxy verwenden. Die nginx- und Caddy-Dateien im Repository sind Beispiele.

Aus dem Repository als Verwaltungsbenutzer starten:

```bash
python3 -B -m dashboard.server \
  --report "$lab_maintenance_report" \
  --lab08b-known-hosts "$lab08b_known_hosts"
```

Die Variablen bezeichnen den privaten lokalen Wartungsbericht und die separat geprüfte SSH-Hostdatei für LAB-08B. Der Dienst ist lokal unter `http://127.0.0.1:8765` erreichbar.

## Pilot, Abnahme und Raumdeploy

1. Aufgabe und einen Pilotclient auswählen, LAB-08A oder LAB-08B.
2. Die Ansible-Aufgaben prüfen und den Lauf starten. Die Webverwaltung speichert Quellstand, Bildauswahl und Laufprotokoll.
3. Die erwarteten Funktionen am Arbeitsplatz testen und die Abnahme eintragen.
4. Den freigegebenen Stand im Raum ausrollen. Ergebnisse je Client prüfen und offene Ziele für denselben Stand nachholen.

Der Bilddeploy prüft SHA-256, Eigentümer, Dateirechte und die relevanten KDE- und SDDM-Einstellungen. Bei vollständiger Übereinstimmung entfallen Übertragung und Sicherung. Bei Änderungen sichert der Lauf den vorherigen Stand und überträgt jede geänderte Bilddatei einmal. Die Kopie des eingespielten Bilds in der Sicherung entsteht lokal auf dem Client. Ein sofortiger Greeter-Neustart setzt eine freie Konsole voraus. Das Desktopbild erscheint in der nächsten KDE-Sitzung.

## Neuinstallation

1. Das Debian-13-Image mit KDE über IServ installieren. Die interne Netzwerkkarte braucht die Schul-IP und die Standardroute.
2. Den neuen SSH-Fingerabdruck prüfen. Ein privates Hostmanifest mit Geräteidentität, USB-Netzwerkkarte, Paketpfaden und Zielrechner vorbereiten.
3. Auf LAB-Teacher `baseline-client.py --dashboard` mit geprüftem Host und Manifest ausführen. Die Erstaufnahme fragt die lokalen Administrator-Zugangsdaten ab.
4. In der Webverwaltung genau diesen Client unter **Neuinstallation** auswählen. Sie installiert die Baseline und danach das lokale Sammelkonto.
5. Nach dem Neustart IServ-Anmeldung, Files, Labornetz, lokale Anmeldung, Desktopbild, USB-Konsole und Wireshark prüfen. Einen Winbind-Patch separat passend zum Paketstand bewerten.

Das Beispielinventar und die Beispielmanifeste dienen als Vorlage. Live-Inventar, SSH-Hostdateien, Hostmanifeste, Passwort-Hash des Sammelkontos und weitere Zugangsdaten bleiben lokal. Beim Sammelkonto ausdrücklich festlegen, ob ein gleichnamiges Verzeichniskonto bewusst verdeckt werden darf.

## Verschlüsselte Sicherung

Die Sicherung umfasst den Projektbaum, den Dashboard-Dienst samt Drop-in, das lokale Dashboard-Passwort, Bildbibliothek und Bildauswahl, die Deploy- und Sammelprotokolle sowie das für die Wiederherstellung erforderliche lokale SSH-Material. Das verschlüsselte Archiv landet auf der lokalen Datenpartition und kann optional per SCP kopiert werden. Das SCP-Passwort selbst ist ausgeschlossen.

Den Wiederherstellungsschlüssel separat aufbewahren und die Wiederherstellung auf einem Testsystem prüfen. Nur für root lesbare TLS-Schlüssel und die Konfiguration eines separaten Reverse-Proxys benötigen eine eigene Sicherung. Die öffentliche GitHub-Kopie ist eine Quellcode-Veröffentlichung.
