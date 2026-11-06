# Package maintenance

[Deutsch](wartung-vorschau.md) · **English**

Run the read-only preview from LAB-Teacher:

```bash
python3 -B -m maintenance.preview --lab08b-known-hosts "$lab08b_known_hosts"
```

The maintenance window is Monday through Friday, 18:00 to 20:00 Europe/Berlin. Work already in progress can finish. The preview checks package state, active sessions, proposed removals and bootable kernels. A scheduled pilot runs within the configured maintenance window. If a user is signed in or an IServ home is still mounted, it downloads packages and defers installation. On an idle machine, it checks the transaction, protects classroom packages and the version-bound Winbind workaround, and restarts only when required.

Review preview output and package versions on the pilot before wider use. The maintenance runner deliberately keeps session, package-manager and reboot checks in place.
