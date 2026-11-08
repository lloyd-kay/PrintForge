# Native one-line installation

## Design

Install PrintForge alongside Klipper/Moonraker on 64-bit Debian 12/13 or Debian-based Raspberry Pi OS with Python 3.11+. A native systemd service avoids Docker/Node on a printer host. A prebuilt release archive contains the React frontend and application; Python dependencies install into a dedicated virtual environment.

The user authorized making the repository public. The bootstrap downloads a pinned release installer with curl; the installer downloads and checks that same release before asking sudo to apply it. Authenticated GitHub CLI downloads remain an option for private forks. Never put tokens in URLs or command arguments.

Paths: `/opt/printforge/releases/<tag>-<archive-digest-prefix>`, `/opt/printforge/current`, `/var/lib/printforge`, `/etc/printforge/printforge.env`, `/etc/systemd/system/printforge.service`. Service account `printforge`; default port 8088 on the trusted LAN. Each release contains its venv at the final absolute path. Existing environment/token/data survive repeat installation. Only PrintForge is restarted. No printer configuration or firmware changes, automatic bed clearance, or print commands.

## Tasks

- Installer: validate platform, dependencies, archive/checksum and owned paths; install into a new release directory; generate initial live settings; switch service; verify health and roll back symlink/unit if activation fails.
- Release: package source, compiled UI, runtime dependencies, installer and checksums; publish a versioned asset in the private repository.
- Verification: regression tests for extraction, config preservation, no unrelated service changes and rollback; Linux shell syntax and Debian dependency checks; actual systemd installation where a disposable environment permits it.
- Documentation: one line for the current private repository, supported platforms, access/login, updates, troubleshooting and remaining setup of printer profiles/Spoolman.

Fetching happens before privilege escalation. Package installation uses list-only needrestart mode and never upgrades the operating system. Binary Python dependencies are required to avoid a surprise compiler workload on the printer host. Keep previous releases for recovery.
