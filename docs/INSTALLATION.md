# Install alongside Klipper in one command

Run this in an SSH terminal on your **64-bit Debian 13 farm server / Klipper host**. The installer also targets Debian 12 and compatible 64-bit Raspberry Pi OS, with Python 3.11 or newer and systemd. It installs PrintForge and Spoolman as separate services and connects accessible Moonraker instances on this host.

```bash
(d=$(mktemp -d) && trap 'rm -rf "$d"' EXIT && curl -fL https://github.com/lloyd-kay/PrintForge/releases/download/v0.2.0/install.sh -o "$d/install.sh" && bash "$d/install.sh")
```

You need `curl`, Python 3 and sudo access. Run the command as your normal SSH user; it asks for sudo when installation needs it. It downloads the release first, checks the archive checksum, installs pinned Python packages in a dedicated environment, creates a `printforge` service account, and starts PrintForge automatically. The dashboard is already compiled, so Node.js and Docker are unnecessary on this host. Python dependencies come from the Python package index.

Open **http://YOUR-SERVER-IP:8088** on your trusted local network. The installer tells you where to retrieve your generated administrator token. The service binds to the LAN by default; keep it behind your LAN/VPN or add a TLS reverse proxy for remote access.

PrintForge starts in live mode with the scheduler paused. The installer discovers local Moonraker services and conventional `printer_data` directories, verifies their existing credentials through the local socket, and registers the connections. It reads the reported nozzle, travel limits and absolute camera URLs when available. New connections are disabled: review the printer type, usable build dimensions, nozzle, slicer profile and loaded spool before enabling. Travel limits are not necessarily the printable bed. Existing configured entries are matched by their Moonraker URL and retain operator settings. Inventory placeholders are kept; give discovered connections meaningful names in Printer setup. Relative camera URLs need a browser-reachable address entered manually.

## Filament tracking

The same command installs the pinned [Spoolman 0.27.1 release](https://github.com/Donkie/Spoolman/releases/tag/v0.27.1), verifies its archive SHA-256, creates a dedicated service account, and starts it on **http://YOUR-SERVER-IP:7912**. Its compiled interface is included; no Docker or Node.js is required. It reuses an existing healthy Spoolman at that port or an inventory already configured in PrintForge/local Moonraker. Conflicting inventory servers stop setup rather than switching databases. Spoolman has no built-in authentication: keep both services on your trusted LAN/VPN.

For each local Moonraker, setup reads the main configuration and included files. It preserves an existing matching `[spoolman]` section, or backs up and appends one. Moonraker is restarted only after two idle/readiness checks; printing, paused, disconnected or uncertain status defers the change/restart. Unrelated settings changed on disk since Moonraker last loaded also defer activation; review and apply those changes yourself before rerunning. Klipper is never restarted. A race between checking and an independently started print cannot be eliminated by Moonraker's API, so run installation while the farm is idle. If setup reports pending work (exit status 2), rerun when idle and connected. There is no background restart retry.

Open Spoolman to add your real filaments/spools, then select the spool physically loaded on each printer in PrintForge. Setup never guesses or selects a physical spool. The integration lets Moonraker report filament consumption and PrintForge read the same inventory.

For a nonstandard Moonraker directory, append `--moonraker-data /home/YOUR-USER/printer_data` to the final `bash "$d/install.sh"` invocation. To reuse an existing Spoolman explicitly, append `--spoolman-url http://YOUR-SPOOLMAN:7912`. The installer preserves existing authentication policy and never rotates Moonraker API keys.

Automatic discovery covers this host. For printers on separate hosts, add their Moonraker URL/key in PrintForge and set their Moonraker `[spoolman]` server to this host's **LAN address**, not `127.0.0.1`. See [Moonraker's Spoolman configuration](https://moonraker.readthedocs.io/en/latest/configuration/#spoolman).

Check the installed services:

```bash
sudo systemctl status printforge printforge-spoolman --no-pager
```

If an existing external Spoolman was reused, its own service name/deployment remains unchanged. Docker/Portainer remains available as a separate [deployment option](DEPLOYMENT.md).

## Updates and settings

Run the installation command again to repair/reinstall this pinned version. Future releases will have their own versioned command. Existing settings, administrator token and job data are preserved. A new release uses a new directory and its own virtual environment; the previous release remains available. If activation fails, the installer attempts to restore the previous service and release.

Append `--port 8089` or `--bind 127.0.0.1` to the final `bash "$d/install.sh"` invocation to choose a different listener. Plain reruns preserve the previous listener settings. Do not run an update while PrintForge is dispatching a job. Klipper continues executing existing prints independently, but PrintForge deliberately pauses scheduling after restart.

| Item | Location |
|---|---|
| Active application | `/opt/printforge/current` |
| Versioned releases | `/opt/printforge/releases/` |
| Database and G-code | `/var/lib/printforge/` |
| Settings and token | `/etc/printforge/printforge.env` (root-readable) |
| Service | `/etc/systemd/system/printforge.service` |
| Native Spoolman application | `/opt/printforge-spoolman/current` |
| Spoolman database, logs and backups | `/var/lib/printforge-spoolman/` |
| Spoolman settings | `/etc/printforge-spoolman/env` |
| Spoolman service | `/etc/systemd/system/printforge-spoolman.service` |

```bash
sudo systemctl status printforge --no-pager
sudo journalctl -u printforge -n 100 --no-pager
sudo systemctl stop printforge
```

To back up, stop PrintForge and copy `/var/lib/printforge` together with `/etc/printforge` to protected storage; restart it afterward. The settings and database can contain credentials. Preserve file ownership and permissions when restoring.

Back up Spoolman separately: stop `printforge-spoolman`, copy `/var/lib/printforge-spoolman` and `/etc/printforge-spoolman`, then restart it. Moonraker configuration backups are beside the original file with `.printforge-backup-...` suffixes. Restoring one requires an idle-time Moonraker restart. PrintForge environment backups stay root-readable in `/etc/printforge`. Setup is staged: a later connection failure leaves successfully installed services available and reports the remaining work; it does not remove their data.

Older vendor-supplied printer operating systems and 32-bit ARM are outside this installer’s tested target. Install on your Debian 13 farm server and connect those printers through Moonraker instead.

## Source and release integrity

The one-liner downloads a pinned installer to a fresh temporary directory before running it. The release includes `SHA256SUMS`, `install.sh` and `printforge-v0.2.0.tar.gz`. Checksums detect corruption; they are not a separate signature or independent proof of publisher identity. You can download and inspect the script before running it.

For a locally downloaded archive, use `bash install.sh --archive /path/to/printforge-v0.2.0.tar.gz`; keep its `SHA256SUMS` beside it. Spoolman and Python dependencies still require network access on a fresh installation.

Platform references: [Moonraker installation](https://moonraker.readthedocs.io/en/latest/installation/), [Python virtual environments](https://docs.python.org/3/library/venv.html).
