# Lokale LAB-Verwaltung

Der erste Stand ist eine Vorschau für LAB-Teacher. Er zeigt den
Wartungszustand der Clients und die Ansible-Aufgaben der Baseline für genau
einen ausgewählten Rechner. Die Schaltfläche zur Aktualisierung führt die
bereits geprüfte lesende Wartungsvorschau erneut aus.

Aus dem Repository als Verwaltungsbenutzer starten:

```bash
python3 -B -m dashboard.server \
  --report "$lab_maintenance_report" \
  --lab08b-known-hosts "$lab08b_known_hosts"
```

Die beiden Variablen enthalten den Pfad zum privaten lokalen JSON-Bericht
und zur separat verifizierten SSH-Hostdatei für LAB-08B. Der Ordner des
Berichts muss bereits existieren. Die Adresse auf LAB-Teacher lautet
`http://127.0.0.1:8765`. Beenden mit Strg+C.

Die Vorschau bindet an Loopback und ist noch kein Dienst für das Schulnetz.
Die Aufgabenliste ruft Ansible ausschließlich mit `--list-tasks` und
einem validierten Einzelziel auf. Die Aktualisierung der Wartungsvorschau
ruft ausschließlich das feste lesende Kommando auf.

Für die nächste Stufe vorgesehen: lokales Verwaltungskonto,
HTTPS für die Erreichbarkeit im Schulnetz, fest definierte Aufträge,
gespeicherte Laufprotokolle und Freigabe der Verteilung nach einem Pilotlauf.
