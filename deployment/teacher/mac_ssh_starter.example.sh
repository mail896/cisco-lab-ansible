#!/bin/sh
# Beispiel für den bestehenden Mac-Expect-Starter. Passwort nur lokal einsetzen.
exec expect -c '
set timeout 30
spawn ssh -tt -p 9010 backup@school.example.org
expect {
    "password:" {
        send -- "MEINPASS\r"
        expect {
            -re {\$ $} {}
            timeout { puts stderr "Kein Shell-Prompt nach Anmeldung."; exit 1 }
            eof { exit 1 }
        }
    }
    -re {\$ $} {}
    timeout { puts stderr "Keine Antwort auf SSH-Anmeldung."; exit 1 }
    eof { exit 1 }
}
send -- "set -o emacs\r"
expect {
    -re {\$ $} {}
    timeout { puts stderr "Eingabemodus nicht bestätigt."; exit 1 }
    eof { exit 1 }
}
interact
'
