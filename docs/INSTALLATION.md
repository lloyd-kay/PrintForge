# Install alongside Klipper in one command

Run this in an SSH terminal on your **64-bit Debian 13 farm server / Klipper host**. The installer also targets Debian 12 and compatible 64-bit Raspberry Pi OS, with Python 3.11 or newer and systemd. It installs PrintForge as a separate service; it does not change or restart Klipper or Moonraker.

```bash
(d=$(mktemp -d) && trap 'rm -rf "$d"' EXIT && curl -fL https://github.com/lloyd-kay/PrintForge/releases/download/v0.1.0/install.sh -o "$d/install.sh" && bash "$d/install.sh")
```

You need `curl`, Python 3 and sudo access. Run the command as your normal SSH user; it asks for sudo when installation needs it. It downloads the release first, checks the archive checksum, installs pinned Python packages in a dedicated environment, creates a `printforge` service account, and starts PrintForge automatically. The dashboard is already compiled, so Node.js and Docker are unnecessary on this host. Python dependencies come from the Python package index.

Open **http://YOUR-SERVER-IP:8088** on your trusted local network. The installer tells you where to retrieve your generated administrator token. The service binds to the LAN by default; keep it behind your LAN/VPN or add a TLS reverse proxy for remote access.

PrintForge starts in live mode with the scheduler paused and all printer entries disabled. In Printer setup, enter your Moonraker connection and verify dimensions, nozzle, profile and loaded spool before enabling a printer. For Moonraker on the same machine, the usual URL is `http://127.0.0.1:7125`; use the actual port if yours differs. Camera URLs must be reachable from your browser.

## Filament tracking

The installer does not install Spoolman or edit `moonraker.conf`. Set `SPOOLMAN_URL` in `/etc/printforge/printforge.env` to your Spoolman server root, then restart only PrintForge:

```bash
sudo systemctl restart printforge
```

Use the [Spoolman deployment instructions](DEPLOYMENT.md#run-spoolman-on-the-same-host) if you need an instance. Each printer's Moonraker must point to that same Spoolman instance. Keep the scheduler paused until the actual loaded spools and profiles are correct.

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

```bash
sudo systemctl status printforge --no-pager
sudo journalctl -u printforge -n 100 --no-pager
sudo systemctl stop printforge
```

To back up, stop PrintForge and copy `/var/lib/printforge` together with `/etc/printforge` to protected storage; restart it afterward. The settings and database can contain credentials. Preserve file ownership and permissions when restoring.

Older vendor-supplied printer operating systems and 32-bit ARM are outside this installer’s tested target. Install on your Debian 13 farm server and connect those printers through Moonraker instead.

## Source and release integrity

The one-liner downloads a pinned installer to a fresh temporary directory before running it. The release includes `SHA256SUMS`, `install.sh` and `printforge-v0.1.0.tar.gz`. Checksums detect corruption; they are not a separate signature or independent proof of publisher identity. You can download and inspect the script before running it.

For a locally downloaded archive, use `bash install.sh --archive /path/to/printforge-v0.1.0.tar.gz`; keep its `SHA256SUMS` beside it.

Platform references: [Moonraker installation](https://moonraker.readthedocs.io/en/latest/installation/), [Python virtual environments](https://docs.python.org/3/library/venv.html).
