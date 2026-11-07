#!/bin/bash
# Retire the failed wake path and prevent further suspend on LAB-Teacher.
set -euo pipefail
if [[ $(id -u) != 0 || $(hostname) != LAB-Teacher ]]; then
    echo 'Nur als root auf LAB-Teacher ausführen.' >&2
    exit 1
fi
units=(sleep.target suspend.target hibernate.target hybrid-sleep.target suspend-then-hibernate.target)
for unit in "${units[@]}"; do
    state=$(systemctl show "$unit" -p ActiveState --value)
    if [[ $state != inactive && $state != failed ]]; then
        echo "Unerwarteter Schlafzustand $unit: $state. Kein Eingriff." >&2
        exit 1
    fi
    if [[ -e /etc/systemd/system/$unit && ! -L /etc/systemd/system/$unit ]]; then
        echo "Lokale Sonderkonfiguration für $unit zuerst prüfen." >&2
        exit 1
    fi
done
backup_dir=$(mktemp -d /var/backups/cisco-lab-teacher-no-suspend.XXXXXX)
chmod 700 "$backup_dir"
for name in "${units[@]}" cisco-lab-teacher-wake.timer cisco-lab-teacher-wake.service; do
    systemctl is-enabled "$name" > "$backup_dir/$name.previous-enable-state" 2>/dev/null || true
    if [[ -e /etc/systemd/system/$name || -L /etc/systemd/system/$name ]]; then
        cp -a -- "/etc/systemd/system/$name" "$backup_dir/$name"
    else
        touch "$backup_dir/$name.previously-absent"
    fi
done
systemctl disable --now cisco-lab-teacher-wake.timer
systemctl mask "${units[@]}"
systemctl daemon-reload
for unit in "${units[@]}"; do
    state=$(systemctl show "$unit" -p LoadState --value)
    [[ $state == masked ]] || { echo "$unit ist nicht maskiert." >&2; exit 1; }
done
printf 'Standby und Ruhezustand am Teacher gesperrt. Rückfallkopie: %s\n' "$backup_dir"
printf 'Kein Suspend, Ausschalten oder Neustart ausgeführt. WOL-/Netzwerkkonfiguration nicht geändert.\n'
