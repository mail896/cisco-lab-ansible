#!/bin/bash
# Einmalige privilegierte Vorbereitung. Keine allgemeinen sudo-Rechte vergeben.
set -euo pipefail
if [[ $(id -u) != 0 || $(hostname) != LAB-Teacher ]]; then
    echo 'Mit sudo ausschließlich auf LAB-Teacher ausführen.' >&2
    exit 1
fi
cd /home/marc/ansible-cisco-lab
test -f maintenance/teacher-backup-system.py
getent passwd marc >/dev/null
/usr/sbin/visudo -c -f /etc/sudoers >/dev/null
backup_dir=$(mktemp -d /var/backups/cisco-lab-teacher-setup.XXXXXX)
chmod 700 "$backup_dir"
for name in /usr/local/sbin/cisco-lab-backup-system /etc/sudoers.d/cisco-lab-system-backup; do
    if [[ -e $name ]]; then cp -a -- "$name" "$backup_dir/$(basename "$name")"; fi
done
install -o root -g root -m 0755 maintenance/teacher-backup-system.py /usr/local/sbin/cisco-lab-backup-system
sudo_rule=$(mktemp /etc/sudoers.d/.cisco-lab-backup.XXXXXX)
trap 'rm -f -- "$sudo_rule"' EXIT
printf '%s\n' 'marc ALL=(root) NOPASSWD: /usr/local/sbin/cisco-lab-backup-system ""' > "$sudo_rule"
chmod 0440 "$sudo_rule"
/usr/sbin/visudo -c -f "$sudo_rule" >/dev/null
mv -- "$sudo_rule" /etc/sudoers.d/cisco-lab-system-backup
/usr/sbin/visudo -c -f /etc/sudoers >/dev/null
install -d -o marc -g marc -m 0755 /home/marc/.config/systemd/user/cisco-lab-dashboard.service.d
install -o marc -g marc -m 0644 maintenance/systemd/dashboard-backup.conf /home/marc/.config/systemd/user/cisco-lab-dashboard.service.d/backup.conf
loginctl enable-linger marc
printf 'Systemdatei-Export für verschlüsselte Backups vorbereitet. Rückfallkopie %s\n' "$backup_dir"
printf 'Der Dashboard-Benutzerdienst kann nun auch ohne KDE-Anmeldung starten.\n'
