#!/bin/bash
# One-time operator step on APP. Prompts for the Mac account password in the terminal.
set -euo pipefail

cd /home/lab-admin/cisco-lab
receiver=deployment/apps/mac-backup-receiver.sh
pin=/var/lib/cisco-lab/.ssh/mac.known_hosts
line=/var/lib/cisco-lab/.ssh/mac-backup-authorized.pub
host=backup@198.51.100.76
expected=SHA256:n4EtZhYxzW6rJ+4coP7u2z4uNDadJ059OeEWO2NQKd4

sudo -n test -r "$pin"
sudo -n test -r "$line"
test -f "$receiver"
actual=$(sudo -n ssh-keygen -lf "$pin")
[[ "$actual" == *"$expected"* ]] || { echo 'Mac-Hostpin stimmt nicht.' >&2; exit 1; }

options=(-T -F /dev/null -o StrictHostKeyChecking=yes
         -o "UserKnownHostsFile=$pin" -o GlobalKnownHostsFile=/dev/null
         -o PubkeyAuthentication=no -o PreferredAuthentications=password,keyboard-interactive
         -o NumberOfPasswordPrompts=1 -o ConnectTimeout=8)

echo 'Mac-Empfänger wird ausschließlich in /Volumes/Repo/cisco-lab installiert.'
sudo -n ssh "${options[@]}" "$host" '
set -eu
umask 077
test -d /Volumes/Repo && test ! -L /Volumes/Repo
mkdir -p /Volumes/Repo/cisco-lab
test ! -L /Volumes/Repo/cisco-lab
chmod 700 /Volumes/Repo/cisco-lab
cd /Volumes/Repo/cisco-lab
test ! -L .receiver.sh
temporary=.receiver.sh.new.$$
test ! -e "$temporary"
cat > "$temporary"
chmod 700 "$temporary"
mv "$temporary" .receiver.sh
' < "$receiver"

echo 'Der auf diesen LAB-Empfänger beschränkte öffentliche Schlüssel wird zugelassen.'
sudo -n cat "$line" | sudo -n ssh "${options[@]}" "$host" '
set -eu
umask 077
mkdir -p "$HOME/.ssh"
chmod 700 "$HOME/.ssh"
touch "$HOME/.ssh/authorized_keys"
chmod 600 "$HOME/.ssh/authorized_keys"
IFS= read -r line
if ! grep -Fqx "$line" "$HOME/.ssh/authorized_keys"; then
    printf "%s\n" "$line" >> "$HOME/.ssh/authorized_keys"
fi
'

echo 'Eingeschränkter Backup-Zugang wird geprüft.'
sudo -n runuser -u cisco-lab -- ssh -T -F /dev/null \
    -i /var/lib/cisco-lab/.ssh/id_ed25519_backup \
    -o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes \
    -o "UserKnownHostsFile=$pin" -o GlobalKnownHostsFile=/dev/null \
    -o ConnectTimeout=8 "$host" check
