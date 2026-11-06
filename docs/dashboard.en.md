# Teacher dashboard operations

[Deutsch](dashboard.md) · **English**

The dashboard runs on LAB-Teacher as a local Python service. It uses the teacher's verified SSH identity for the LAB clients. Keep the HTTP listener on loopback and use an authenticated HTTPS reverse proxy if operators need remote access. The included nginx and Caddy files are configuration examples.

Start the service from the repository as the management user:

```bash
python3 -B -m dashboard.server \
  --report "$lab_maintenance_report" \
  --lab08b-known-hosts "$lab08b_known_hosts"
```

The variables point to the private maintenance report and the separately verified SSH host-key file for LAB-08B. Open `http://127.0.0.1:8765` on LAB-Teacher.

## Pilot, review and release

1. Choose an operation and one test workstation, LAB-08A or LAB-08B.
2. Review the generated Ansible task list and start the pilot. The dashboard records the source revision, images and output.
3. Test the expected behaviour at the workstation, then record the acceptance in the dashboard.
4. Start the room rollout for the accepted revision. Check each client's result and retry pending targets for that revision.

Image deployment checks SHA-256, ownership, file permissions and the relevant KDE and SDDM settings. A matching client skips image transfers and snapshots. On a change, the previous files are snapshotted. Each changed image is sent once and copied into the run's backup on the client. An immediate greeter refresh requires a free local console. The existing desktop session sees a new wallpaper at its next sign-in.

## Fresh installation

1. Install the Debian 13 KDE image through IServ. The internal network adapter must carry the school DHCP address and default route.
2. Verify the newly installed host's SSH fingerprint. Prepare a private client manifest with the device identity, USB adapter, package inputs and target host.
3. Run the SSH admission stage on LAB-Teacher with `baseline-client.py --dashboard` and the reviewed host and manifest arguments. It prompts for the local administrator credentials.
4. In the dashboard, select that single workstation under fresh installation. This applies the baseline and provisions the local classroom account.
5. After reboot, verify an IServ sign-in, Files access, the lab dialog and a separate local classroom sign-in. Check the desktop, serial console and Wireshark. Assess any version-bound Winbind workaround separately.

The public sample inventory and network manifests are starting points. Keep the live inventory, verified host-key files, manifests, the classroom password hash and other runtime credentials in the local installation. The local classroom account playbook requires a locally generated password hash and an explicit decision about a matching directory account.

## Encrypted backup

The backup job captures the project tree, dashboard service and drop-in configuration, dashboard credential, wallpaper library and selection, deployment and room logs, and local SSH material needed for recovery. The archive is encrypted and saved on the local data volume. An optional SCP copy can be enabled for the configured backup host. The SCP credential is excluded from the archive.

Store the backup recovery key separately and verify a restore on a disposable machine. Reverse-proxy TLS material that is readable only by root, the recovery key itself and the off-host proxy configuration need their own recovery procedure. GitHub is a source publication and is separate from the operational backup.
