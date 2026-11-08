# Deploy and operate PrintForge

## Debian 13 and Docker Compose

Install Docker Engine and the Compose plugin from Docker’s apt repository for Debian 13 (Trixie). Verify `docker version` shows both a client and a server, and `docker compose version` shows the Compose plugin. [Docker Engine on Debian](https://docs.docker.com/engine/install/debian/)

Run these commands from the PrintForge source directory on the Docker host:

```sh
docker compose up -d --build
docker compose ps
curl --fail http://127.0.0.1:8000/api/health
```

The health endpoint reports `status: ok`, the app version, and the current mode. A healthy status means the API is responding. Check the dashboard separately for Moonraker and Spoolman connectivity.

The default host binding is `127.0.0.1:8000`. To open it from your own computer while keeping that binding, forward it over SSH with `ssh -L 8000:127.0.0.1:8000 user@docker-host` and visit [PrintForge](http://127.0.0.1:8000/). You can also bind to a trusted LAN interface or put it behind a TLS reverse proxy. Keep live access on a trusted LAN or VPN and use HTTPS for remote access.

## Configure a live instance

Use a separate Compose project for live mode so it gets its own database and upload volume. Copy `.env.example` to `.env.live` and make it readable only by the deploying account (`chmod 600 .env.live` on Linux).

| Variable | Setting |
| --- | --- |
| `PRINTFORGE_MODE` | Set to `live`. The default `demo` uses simulated printers and simulated spool inventory. |
| `PRINTFORGE_TOKEN` | Set to a unique random administrator token of at least 24 characters. For example, use the output of `openssl rand -hex 32`. The demo token is rejected in live mode. |
| `SPOOLMAN_URL` | Spoolman server root reachable from the container, such as `http://spoolman.lan:7912`. Omit the `/api/v1` suffix. A blank value allows startup, but live dispatch requires fresh spool inventory. |
| `PRINTFORGE_POLL_SECONDS` | Telemetry and scheduling interval in seconds. Defaults to `5`, with a minimum of `1`. |
| `PRINTFORGE_BIND` | Host address to publish on. Defaults to `127.0.0.1`. Use the host’s trusted LAN address if needed. |
| `PRINTFORGE_PORT` | Published host port. Defaults to `8000`. Use another port if demo and live run simultaneously. |
| `PRINTFORGE_DATA_DIR` | Set by Compose to `/data` inside the container. The local named volume holds SQLite and uploaded files. |

```sh
docker compose --env-file .env.live -p printforge-live up -d --build
docker compose --env-file .env.live -p printforge-live ps
curl --fail http://127.0.0.1:8000/api/health
```

Adjust the health URL if you changed the published address or port. Confirm the response shows `live` before configuring printers. The administrator token grants full access. Treat the environment file, Moonraker API keys, and database backups as secrets.

## Run Spoolman on the same host

If you already run Spoolman, point PrintForge and each printer’s Moonraker at that instance. For a new installation, [compose.spoolman.yaml](../compose.spoolman.yaml) adds the official Spoolman 0.27.1 image and a local named volume for its SQLite data. The standard PrintForge Compose command starts only PrintForge. [Spoolman 0.27.1 release](https://github.com/Donkie/Spoolman/releases/tag/v0.27.1)

The default Spoolman binding is `127.0.0.1:7912`. For printers on the network, set `SPOOLMAN_BIND` to the Debian host’s trusted LAN address. The examples use `192.168.1.20`. Replace it with your address. Set `SPOOLMAN_PUBLISHED_PORT` if you need a different host port. Spoolman has no built-in authentication, so keep access on the trusted network. [Spoolman network security](https://github.com/Donkie/Spoolman/wiki/Security)

**One Compose project.** Set these values in `.env.live` alongside the live settings:

```dotenv
SPOOLMAN_URL=http://spoolman:8000
SPOOLMAN_BIND=192.168.1.20
```

```sh
docker compose --env-file .env.live -p printforge-live \
  -f compose.yaml -f compose.spoolman.yaml up -d --build
```

PrintForge uses the `spoolman` service name and container port `8000` within that Compose project. Use both Compose files for future updates to the combined project.

**Separate deployment, including with Portainer.** Run Spoolman as its own Compose project on the Docker host:

```sh
SPOOLMAN_BIND=192.168.1.20 docker compose -p spoolman \
  -f compose.spoolman.yaml up -d
```

You can also upload `compose.spoolman.yaml` as its own Portainer stack and set `SPOOLMAN_BIND` there. Set `SPOOLMAN_URL=http://192.168.1.20:7912` in the separate PrintForge project or Portainer stack so its container reaches Spoolman through the published LAN address.

**Moonraker on each printer.** Use the Debian host’s LAN address and published port in `moonraker.conf`. The `spoolman` service name is local to the combined Docker network.

```ini
[spoolman]
server: http://192.168.1.20:7912
```

Restart Moonraker after changing its configuration. Open Spoolman at the published address, add the actual spools, and verify that PrintForge sees them and Moonraker can reach the same instance. [Moonraker Spoolman configuration](https://moonraker.readthedocs.io/en/latest/configuration/#spoolman)

The named volume mounts at `/home/app/.local/share/spoolman`. If you replace it with a bind mount on Debian, create the directory first and give UID and GID `1000` write access, as documented by Spoolman. Back up Spoolman’s database separately from PrintForge. [Spoolman Docker installation](https://github.com/Donkie/Spoolman/wiki/Installation) · [Spoolman backup and restore](https://github.com/Donkie/Spoolman/wiki/Backup-and-Restore)

## Portainer on Docker Standalone

1. On the Docker endpoint managed by Portainer, build the image from the source directory:

   ```sh
   docker build -t printforge:0.2.0 .
   docker image inspect printforge:0.2.0
   ```

   If you built on another machine, use `docker save` and `docker load` to transfer an image for the endpoint’s architecture. The supplied stack uses the local image. For a registry you control, publish it there, set `PRINTFORGE_IMAGE` to its full registry tag, and change the stack’s `pull_policy` to `missing`. There is no published PrintForge image for this delivery. [Portainer’s guidance for external builds](https://docs.portainer.io/faqs/known-issues/docker-compose-files-including-build-steps-fail)

2. In Portainer, select the Docker Standalone environment and open **Stacks → Add stack**. Upload [portainer-stack.yaml](../portainer-stack.yaml) or paste it into **Web editor**. Give demo and live deployments distinct stack names so each uses a separate volume. [Add a stack in Portainer](https://docs.portainer.io/user/docker/stacks/add)
3. Add the environment variables from the table above or load them from a local environment file. For a demo, the defaults are sufficient. If accessing the service from another machine, use an SSH tunnel, a trusted LAN address, or a TLS reverse proxy.
4. Deploy the stack and check the container logs and the health endpoint. Run one container per data volume. The same source configuration applies when redeploying.

## Bring printers online

The starting fleet is reported to use Klipper. Verify the Moonraker endpoint and actual hardware on each machine before enabling dispatch.

| Fleet entry | M1 treatment |
| --- | --- |
| Qidi X Plus3 | FDM candidate. Verify Moonraker access, installed nozzle, build volume and start/end macros. |
| CR-10 #1 and #2 | Two FDM candidates. Share a profile only after confirming identical G-code and macro compatibility. |
| Ender 3 | FDM candidate. Verify the actual configuration and assign its own profile unless compatibility is established. |
| CR-30 | Belt printer, inventory only. |
| Resin printer | Inventory only. |

1. Leave the scheduler paused. Configure one FDM printer with its Moonraker URL and API key if required, verified XYZ travel, nozzle, and an explicit profile ID. Match that ID to the slicer profile used for the uploaded file, including the start and end macros.
2. Configure Moonraker’s Spoolman integration on the printer and assign the actual loaded spool in PrintForge. Enter the matching material and colour. Verify live status and current spool inventory. Moonraker reports filament use to Spoolman. [Moonraker Spoolman configuration](https://moonraker.readthedocs.io/en/latest/configuration/#spoolman)
3. Upload a small G-code file you have checked for that printer. Queue one copy with its real dimensions, nozzle, material, estimated grams and duration. A scheduled time makes the job eligible at that time. Actual dispatch depends on the compatible printer, spool and bed being ready.
4. Inspect the printer in person and remove any parts or obstructions. Mark the bed clear, enable scheduling, and supervise the first print. Compare the displayed job state and Spoolman consumption with what happened on the machine.
5. Clear the bed again between copies. Restart or lost printer connectivity invalidates clearance. If PrintForge holds a job for inspection, check the printer and exact file before resolving it. It holds the reservation while the outcome is uncertain and does not resend the start command.

Use PrintForge as the sole dispatcher for managed printers. A job profile is your assertion that the sliced file is safe on that printer. Size and nozzle checks alone cannot verify macro compatibility or G-code safety.

### Filament inventory

The filament view shows the remaining grams reported by Spoolman (`remaining_g`) and the grams the scheduler can allocate (`available_g`). The difference (`reserved_g`) includes active reservations and amounts PrintForge is still holding against possible unreported usage. A held amount can outlast the job that created it.

Before dispatch, PrintForge saves a deduction for the job’s estimated grams in a persistent budget for that spool. Automatic Spoolman reads can lower the budget. This keeps an old Spoolman balance from freeing grams while a Moonraker usage update is delayed or in flight. Moonraker’s pending reports can be empty while the update is still in flight. Moonraker writes usage to Spoolman, and PrintForge reads the balance.

Use accurate slicer estimates. If an overestimate or refill leaves too little filament available for scheduling, wait until the assigned printer is idle and finish or resolve active jobs for the spool. Verify that Spoolman reflects all completed usage or weigh the spool to confirm the remaining filament and correct its Spoolman balance as needed. Then choose **Reconcile inventory** for that spool. The action fetches fresh inventory and resets the scheduling budget to the balance you have verified.

### Cameras

Enter the actual MJPEG stream URL for each camera. Test the URL in the browser that will open PrintForge, since the browser loads the stream directly. A remote browser needs LAN or VPN reachability, and HTTPS pages may block HTTP camera streams. The MVP has no camera relay or WebRTC gateway.

## Back up and recover

PrintForge runs as UID and GID `10001` and needs write access to `/data`. The supplied Compose files use a local named volume. If you substitute a host bind mount, give UID/GID `10001` write access to that directory before starting. Keep SQLite and the G-code library together on local storage with one application process and one Uvicorn worker.

Pause scheduling and let active prints finish. Stop PrintForge before copying its volume so SQLite and the upload library are backed up together. For the Compose live deployment on Debian:

```sh
docker compose --env-file .env.live -p printforge-live stop printforge
docker volume inspect printforge-live_printforge-data
mkdir -p -m 700 backups
archive=printforge-$(date -u +%Y%m%dT%H%M%SZ).tar.gz
docker run --rm \
  --mount type=volume,src=printforge-live_printforge-data,dst=/data,readonly \
  --mount "type=bind,src=$PWD/backups,dst=/backup" \
  alpine tar -C /data -czf "/backup/$archive" .
tar -tzf "backups/$archive"
docker compose --env-file .env.live -p printforge-live start printforge
```

For Portainer, stop the stack and use the volume name shown under **Volumes**. Run the archive command on the Docker host, then start the stack after verifying the archive. Store a protected copy of the live environment settings separately.

To recover, stop the application and extract the full archive into a new empty local volume, preserving ownership. Use that volume as `/data` on a single replacement instance. Confirm the app starts, the expected records and uploads appear, and the scheduler is paused. Inspect the printers and reconcile any held jobs before enabling it.

Local tests ran on Python 3.14. The supplied Docker configuration has been checked for Compose interpolation and structure. The Python 3.12 Docker build and real-printer acceptance checks remain pending.
