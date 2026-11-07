# Local LAB management

[Deutsch](dashboard.md) · **English**

Updated 7 November 2026. The dashboard runs on LAB-Teacher as the `marc`
systemd user service. HTTPS passes through Caddy and nginx to a loopback-only
Python service. Its web account is `lab-admin`; the password is stored locally
in `~/.local/state/cisco-lab-dashboard/passwort`. Credentials stay out of Git.
The dashboard does not modify the central IServ server.

## Pilot and classroom deployment

1. Select LAB-08A/B and the job under **Pilot deployments** and review its tasks.
2. Use an idle test workstation for desktop, session and package changes.
   The client checks active sessions, IServ mounts and package maintenance.
   Any explicitly requested normal logout must finish before installation.
3. Run the pilot and inspect its persistent Ansible log.
4. Test the listed checks and accept the latest successful pilot of the current
   source revision. A source change requires a new pilot. Older runs remain in
   history and do not replace current acceptance.
5. Deploy the accepted revision under **Playbooks** or **Desktop images**.
   Results are recorded per workstation; pending hosts can receive the same
   approved revision later.

YAML is maintained separately. The GUI exposes predefined jobs and settings.
The GUI and scheduled controller share a cross-process management lock.

## Provisioning a fresh IServ image

1. The operator installs Debian 13 KDE through IServ. The chosen student-home
   mode is `tmpfs (non persistent)`. The internal NIC needs the school DHCP
   address and default route. Image installation can require temporarily
   unplugging the USB lab NIC.
2. Independently verify the new ED25519 fingerprint at the client. A changed
   host key is not trusted automatically. Review the private hardware manifest,
   including both NICs, connection UUIDs and MAC addresses.
3. Use the GUI's fresh-installation SSH admission with the known local
   administrator password. Passwords are not saved in JSON state or run logs.
   Admission subsequently verifies key access and sudo.
4. Start installation for the prepared targets. Two clients can run in parallel;
   additional clients wait in the queue. Stop requests take effect between
   completed phases and never kill an active package transaction.
5. Test local `cisco` and IServ sign-in, Files access, wallpaper, an icon-free
   desktop, lab networking, serial console and Wireshark.

The shared `lab-baseline.yml` contains:

| Phase | Components |
| --- | --- |
| Base | local PAM separation, mandatory Winbind fix, networking, WOL, serial tools, Wireshark, Packet Tracer, KDE, U-Zeiten, Firefox and optional Veyon |
| Classroom account | local `cisco`, LAB permissions, desktop policy and no SSH access |
| Maintenance | evening policy, APT coordination, timer and controller registration |

The GUI resumes at phase boundaries. Retrying failed maintenance does not rerun
an already successful base or classroom-account phase. The CLI uses the same
components. A full LAB-08A run completed 206 tasks without failures. A new
end-to-end GUI run on another freshly imaged workstation is still pending after
these latest changes; queue and recovery behavior have local regression tests.

## Winbind and local accounts

The version-bound reconnect fix is mandatory. Installation checks the exact
supported Samba version and known stock/patched module hashes, backs up the
original, installs atomically and verifies user lookup, LAB membership, machine
trust and the loaded module. Failed verification restores the original.
Unknown versions are not overwritten. The binary and account configuration are
local inputs and are not published.

Only local `/etc/passwd` accounts skip the IServ home-mount PAM step. Directory
users continue to use the existing IServ integration. This prevents accidental
IServ RAM homes for the local management administrator.

## Evening maintenance

The start window is Monday–Friday, 18:00–20:00 Europe/Berlin. Transactions already
in progress may finish. The new mode first requests normal logout and checks
session completion and leftover RAM-home mounts. It then installs updates,
performs guarded package cleanup and always reboots after success. Failures
leave the workstation on. Ambiguous leftover mounts in archive mode are retained
for investigation.

The new policy is active on LAB-08A. LAB-08B retains the earlier comparison
policy, which downloads only while a session is occupied. Other clients will be
registered after their gradual reinstallation. Ordinary logout does not yet
reliably provide a new empty RAM home every time; reboot removes it. Files and
Groups remain on IServ. The profile-deletion feature has been dropped.

The central controller accepts only enrolled workstations with a checked school
MAC and pinned SSH key. It sends WOL if needed, monitors maintenance and proves
the reboot using a changed boot ID. Only a demonstrably newly woken, still idle
workstation may then power off. Clients successfully handled today are not woken
again for another scheduled run. Results appear on the maintenance page. The real
LAB-08A WOL/maintenance/reboot/shutdown trial passed. Central scheduling is enabled
for this enrolled pilot; automatic teacher-PC wake and operation without a KDE
session still need to be secured separately.

## Room actions and images

Room actions provide Magic-Packet WOL, normal student logout, reboot/shutdown
of idle workstations and one-time sign-in of the local `cisco` account. They do
not use Veyon remote control. Deferred power actions recheck sessions, mounts
and maintenance immediately before execution.

PNG/JPEG images are selected from `~/Bilder/CISCO-LAB`. Saving the selection does
not deploy it. Pilot, acceptance and room rollout install the approved revision.
Desktop and lock images are separate; the lock image is also used by SDDM.
An idle greeter can refresh immediately. The desktop updates at the next sign-in.
Unchanged image revisions skip retransmission and another snapshot.

## Encrypted backup

New backups contain project sources, private configuration, SSH material,
wallpaper library, user services and dashboard state including credentials and
run history. Encrypted archives reside on `data/backup_data`; optional SCP sends
the archive and SHA-256 file to the configured macOS host. The SCP password file
and recovery key are excluded. Store the recovery key separately off the teacher PC.

The 7 November archive was restored in isolation: 383 files, matching SSH key
and a restored dashboard returning HTTP 200. This tests configuration recovery,
not a complete replacement computer. Older 6 November backups do not include
complete dashboard state.

Root-only TLS/Veyon configuration requires the fixed
`maintenance/teacher-backup-system.py` exporter and an exact no-argument sudo
allowlist. Teacher setup is prepared and still requires the administrator to
run it. Each backup record shows whether this system export was included.
The dashboard user service must permit that specific sudo invocation.

Retention preserves 90 days plus the latest explicitly confirmed working
backup. Without confirmation, the newest successful backup is preserved.
Confirmation requires a matching checksum. Only known archive pairs are pruned;
failed off-host pruning preserves the local copy. Public GitHub sources and
documentation are separate from operational recovery backups.
