# Package maintenance

The preview checks package state, active sessions, proposed removals and bootable kernels. A scheduled pilot runs within the configured maintenance window. If a user is signed in or an IServ home is still mounted, it downloads packages and defers installation. On an idle machine, it checks the transaction, protects classroom packages and the version-bound Winbind workaround, and restarts only when required.

Review preview output and package versions on the pilot before wider use. The maintenance runner deliberately keeps session, package-manager and reboot checks in place.
