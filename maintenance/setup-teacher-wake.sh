#!/bin/bash
# Install the fixed system wake timer; this never suspends or powers off the PC.
set -euo pipefail
if [[ $(id -u) != 0 || $(hostname) != LAB-Teacher ]]; then
    echo 'Nur als root auf LAB-Teacher ausführen.' >&2
    exit 1
fi
cd /home/marc/ansible-cisco-lab
systemd-analyze verify maintenance/systemd/cisco-lab-teacher-wake.service maintenance/systemd/cisco-lab-teacher-wake.timer
backup_dir=$(mktemp -d /var/backups/cisco-lab-teacher-wake.XXXXXX)
chmod 700 "$backup_dir"
for name in cisco-lab-teacher-wake.service cisco-lab-teacher-wake.timer; do
    if [[ -e /etc/systemd/system/$name || -L /etc/systemd/system/$name ]]; then
        cp -a -- "/etc/systemd/system/$name" "$backup_dir/$name"
    else
        touch "$backup_dir/$name.previously-absent"
    fi
    systemctl is-enabled "$name" > "$backup_dir/$name.previous-enable-state" 2>/dev/null || true
    install -o root -g root -m 0644 "maintenance/systemd/$name" "/etc/systemd/system/$name"
done
systemctl daemon-reload
systemctl enable --now cisco-lab-teacher-wake.timer
systemctl list-timers cisco-lab-teacher-wake.timer --no-pager
echo "Teacher-Aufwachzeit Mo–Fr 17:50 eingerichtet. Rückfallkopie: $backup_dir"
echo 'Hardware-Aufwachtest steht noch aus. Kein automatisches Einschalten aus S5 zugesichert.'
