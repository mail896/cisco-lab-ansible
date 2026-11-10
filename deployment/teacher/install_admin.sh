#!/bin/sh
set -eu
[ "$(id -u)" = 0 ] && [ "$(hostname -s)" = LAB-Teacher ] || exit 1
public_key=${1:?Öffentlicher Teacher-Administrationsschlüssel erforderlich}
case "$public_key" in 'ssh-ed25519 '*) ;; *) exit 1 ;; esac
/usr/sbin/sshd -t
[ ! -L /root/.ssh ] && [ ! -L /root/.ssh/authorized_keys ]
install -d -o root -g root -m 700 /root/.ssh
touch /root/.ssh/authorized_keys
chown root:root /root/.ssh/authorized_keys
chmod 600 /root/.ssh/authorized_keys
key='restrict,from="203.0.113.200" '"$public_key"
grep -qxF "$key" /root/.ssh/authorized_keys || printf '\n%s\n' "$key" >> /root/.ssh/authorized_keys
policy=$(/usr/sbin/sshd -T -C user=root,host=apps,addr=203.0.113.200,laddr=203.0.113.100,lport=22 | awk '$1=="permitrootlogin" {print $2}')
case "$policy" in
  no|forced-commands-only)
    conf=/etc/ssh/sshd_config
    original=$(mktemp /run/cisco-lab-sshd.XXXXXXXX)
    trap 'rm -f -- "$original"' EXIT
    cp -p -- "$conf" "$original"
    if [ ! -e "$conf.before-cisco-lab-teacher-admin" ]; then cp -p -- "$conf" "$conf.before-cisco-lab-teacher-admin"; fi
    printf '\n%s\n' '# CISCO-LAB Teacher operator key from APP' >> "$conf"
    printf '%s\n' 'Match User root Address 203.0.113.200' '    PermitRootLogin prohibit-password' '    PasswordAuthentication no' '    KbdInteractiveAuthentication no' >> "$conf"
    if ! /usr/sbin/sshd -t; then cat "$original" > "$conf"; exit 1; fi
    systemctl reload ssh.service
    ;;
esac
printf '%s\n' 'Teacher-Administrationszugang für APP eingerichtet.'
