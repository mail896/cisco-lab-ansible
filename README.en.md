# CISCO-LAB

**English** · [Deutsch](README.md)

**One room. Two networks. One console.**

A central web application for a Debian/KDE networking classroom with IServ: enroll devices, follow lessons, deploy software and manage maintenance.

The classroom contains 16 student PCs in eight two-person teams and a teacher PC. Each client's internal NIC connects to the school network; a USB Ethernet adapter connects to the separate training network with real switches and routers. The APP console runs as its own service on a separate server.

[Pages](https://mail896.github.io/cisco-lab-ansible/en.html) · [User guide and workflows](docs/dashboard.en.md) · [Package maintenance](docs/wartung-vorschau.en.md)

![Features](docs/media/dashboard-overview.jpg)

## Features

### Room overview

The actual classroom in one shared view. The shared room diagram represents 16 student clients in eight teams and a separate teacher PC. Four desk islands, entrance/window labels and opposing A/B seats make devices easy to locate. The pilot devices remain identifiable as their own team.

### IServ and roles

One sign-in with explicit permissions. The landing page opens school authentication through “IServ Login”. The designated administrator manages devices, software, maintenance and backups. Members of the staff group Kollegium can read the permitted views and execute all bulk actions.

### New installation

From a fresh IServ image to a configured LAB PC. The operator starts OS deployment through IServ. The APP workflow expects a freshly installed Debian 13 device with KDE and the intended nonpersistent tmpfs home setup. APP does not install the operating system or initiate network boot.

![New installation](docs/media/dashboard-installation.jpg)

### Student clients

Inventory and available package updates. The client table combines saved device and package information. Available updates can be expanded to inspect package names. An unread or failed package inventory is not presented as zero updates.

### Bulk actions

Control individual seats, teams or the whole room. Tick individual devices or click a team tile to select both seats together. Shortcuts select all student clients, enrolled devices only, or include the teacher. The teacher is excluded from the default selection.

![Bulk actions](docs/media/dashboard-room.jpg)

### Teaching view

See USB lab networks live. The teaching view uses the same room diagram as the overview, bulk actions and software selection. Each device shows USB link state, detected adapter model, IPv4/IPv6 addresses and route counts. Graphical-session users appear beside their seats when their state could be read.

![Teaching view](docs/media/dashboard-classroom.jpg)

### Repository software

Find, preview and deploy packages. Search the Debian package catalogue by name or short description and tick the required packages. An empty search does not list the entire archive. “Katalog neu einlesen” imports existing package lists from an enrolled client; searching itself does not query clients or install software.

![Repository software](docs/media/dashboard-software-search.jpg)

### Playbook groups

Compose workflows from existing modules. On the Playbooks page, name a group, select registered task modules and approved software profiles, and set their order. A group is a saved composition; it does not alter the mandatory base configuration.

### Pilot and room rollout

Review changes before distributing them. For registered individual tasks, select a preview and pilot client. The pilot run provides a log; practical visual checks are then recorded as acceptance.

### Maintenance

Central scheduling with clear outcomes. APP schedules evening maintenance Monday to Friday within the 18:00–20:00 Europe/Berlin start window. Student clients do not have a competing local schedule. The controller considers enrolled devices and can request powered-off devices through WOL.

### Desktop images

Manage desktop and login backgrounds. Upload images, browse the library and choose KDE desktop and SDDM login backgrounds independently. The page displays thumbnails and the current selection.

### Backup and recovery

Choose APP, Mac and ZIP independently. APP copy, external Mac copy and ZIP download can be selected independently. The browser remembers the selection; a new browser defaults to APP only. APP and Mac each show their storage path and latest backup timestamp.

![Backup and recovery](docs/media/dashboard-backup.jpg)

## Technical entry point

The public sources use consistent documentation addresses and example domains. Operational infrastructure values and their mapping stay in the private project. The full Git tree and image approvals are checked before public commits and pushes; GitHub Actions also validates the published source tree.

The sources target Debian 13, KDE Plasma and the specific IServ/LAB environment. This is an inspectable reference implementation rather than a universal one-click installer. Inventory, OIDC client, role mapping, hardware manifests and private packages must match the deployment. Cisco Packet Tracer, private keys and school credentials are not included in the public repository.

1. Read [architecture and setup](docs/dashboard.en.md#technical-setup) and use `inventory.example.ini` as the inventory template.
2. Set up the separate APP service with its own runtime account, private state and HTTPS/OIDC; templates are in `deployment/apps/`.
3. Enroll one fresh LAB client through the GUI, apply the baseline and record practical on-site acceptance.
4. Enroll further clients and use the required management workflows.

OS deployment and required IServ/OIDC settings remain operator responsibilities.

## Repository layout

| Path | Contents |
| --- | --- |
| `dashboard/` | Web server, OIDC, workflows and shared UI components |
| `roles/`, `tasks/`, `*.yml` | Ansible baseline and registered tasks |
| `network/` | Lab network dialog, privileged network service and capture |
| `maintenance/` | APP controller, package planning and teacher control |
| `deployment/` | Service templates, restricted receivers, export and recovery |
| `tests/` | Backend, browser and client integration tests |
| `docs/` | DE/EN guides, Pages and real screenshots |

## Real APP screenshots

Screenshots show the running German application in a controlled administrative view. Names of graphical-session users are hidden for publication. The English documentation describes the same features; the application itself is in German.

Additional desktop and lab-network images show actual LAB clients; they document client tools rather than a remote-screen feature.

## Version and contact

Documented APP version: **2026.11.09**. 2026 - Marc Schulz · [E-Mail](mailto:marc.schulz@bbs-einbeck.de)
