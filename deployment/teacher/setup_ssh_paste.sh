#!/bin/sh
# Als jeweiliger SSH-Benutzer auf dem Linux-Ziel ausführen.
set -eu

rc="${HOME:?HOME fehlt}/.bashrc"

if [ -L "$rc" ] || { [ -e "$rc" ] && [ ! -f "$rc" ]; }; then
    echo 'Abbruch: .bashrc ist keine reguläre Datei.' >&2
    exit 1
fi
tmp=$(mktemp "${rc}.XXXXXX")
trap 'rm -f "$tmp"' EXIT
trap 'exit 1' HUP INT TERM
if [ -f "$rc" ]; then
    cp -p "$rc" "$tmp"
    # Ersetze auch frühere Versionen, ohne fremde Einstellungen zu entfernen.
    awk '
        $0 == "# BEGIN CISCO-LAB SSH TERMINAL" {
            if (skip) exit 1
            skip=1; next
        }
        $0 == "# END CISCO-LAB SSH TERMINAL" {
            if (!skip) exit 1
            skip=0; next
        }
        !skip {
            if ($0 ~ /^[[:space:]]*$/) { blanks=blanks $0 "\n"; next }
            printf "%s%s\n", blanks, $0
            blanks=""
        }
        END { if (skip) exit 1 }
    ' "$rc" > "$tmp"
fi
cat >> "$tmp" <<'BASH'

# BEGIN CISCO-LAB SSH TERMINAL
if [[ -n ${SSH_TTY:-} && -t 0 && -t 1 && ${TERM:-dumb} != dumb ]]; then
    set -o emacs
    bind 'set enable-bracketed-paste on'
fi
# END CISCO-LAB SSH TERMINAL
BASH
mv "$tmp" "$rc"
echo 'Emacs-Eingabemodus und Einfüge-Fix gespeichert. Wirkt ab der nächsten SSH-Anmeldung.'
