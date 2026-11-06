# LAB-Paketwartung

**Deutsch** · [English](wartung-vorschau.en.md)

Die Vorschau läuft lesend von LAB-Teacher aus. Sie prüft per SSH die vorhandenen Paketlisten, aktive Sitzungen, geplante Paketentfernungen und startfähige Kernel.

```bash
python3 -B -m maintenance.preview --lab08b-known-hosts "$lab08b_known_hosts"
```

Das Wartungsfenster ist werktags von 18:00 bis 20:00 Uhr in Europe/Berlin. Ein begonnener Lauf darf fertig werden. Bei einer aktiven Anmeldung oder eingebundenem IServ-Home lädt der Wartungspilot Pakete nur herunter und verschiebt die Installation. Auf einem freien Rechner prüft er die Transaktion, schützt die LAB-Software und den versionsgebundenen Winbind-Patch und startet nur bei Bedarf neu.

Wenn `autoremove` die LAB-Software, den laufenden Kernel oder den letzten startfähigen Ersatzkernel gefährdet, wird die Paketentfernung ausgesetzt. Vorschau und Paketstand zunächst auf dem Wartungspilot prüfen, bevor der Ablauf auf weitere Rechner ausgeweitet wird.
