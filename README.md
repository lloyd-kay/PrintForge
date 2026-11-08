# PrintForge

PrintForge is a free, self-hosted dashboard and print queue for Klipper farms. It sends already-sliced G-code to Moonraker and tracks a conservative filament budget based on Spoolman inventory. A batch creates one queue entry per physical copy. PrintForge has no per-printer subscription or built-in printer-count limit.

## Run the demo

For a native installation on your Debian 13 Klipper host, use the [one-line installer](docs/INSTALLATION.md). It installs PrintForge and Spoolman without Docker or Node.js, connects local Moonraker instances, and preserves existing inventory. New printer connections remain disabled until their profile and loaded spool are reviewed.

From the project directory, with Docker Engine or Docker Desktop and the Compose plugin running:

```sh
docker compose up -d --build
```

Open [PrintForge](http://127.0.0.1:8000/) on the Docker host and sign in with `printforge-demo`. The demo starts with simulated printers, spools, and sample G-code. Queue a copy using the matching printer profile, mark the simulated bed clear, then enable the scheduler. You can watch the job progress and review its history. Bed clearance is consumed for each copy.

```sh
docker compose ps
docker compose logs -f printforge
docker compose stop
```

Use the [deployment guide](docs/DEPLOYMENT.md) to run on Debian 13, deploy through Portainer, or connect a real printer. It includes an [optional Spoolman container](docs/DEPLOYMENT.md#run-spoolman-on-the-same-host), the live configuration, a first-print checklist, and volume backup steps.

## What’s in this version

| Milestone | Scope |
| --- | --- |
| M1 · MVP | Fleet dashboard, configured MJPEG camera streams, G-code library, central batch queue, scheduled starts, compatibility checks, filament reservations, and job history. Demo and live adapters share the scheduler. |
| M2 · Hardware pilot | Verify Moonraker controls, recovery and real filament usage on one printer, then a small group with compatible profiles. |
| M3 · Farm operations | Expand telemetry, roles, audit, and camera access. Revisit the database and worker architecture as scaling needs become clear. |
| M4 · AI monitoring | Evaluate local print-failure detection and operator-controlled responses. AI detection is planned. |
| M5 · Release and analytics | Add reporting, notifications, maintenance, and a reproducible release and upgrade process. |

M1 takes sliced `.gcode` files up to 50 MiB. It supports earliest-start times and checks printer profile, nozzle, job dimensions, material, optional colour, and available spool grams. The scheduler requires a fresh manual bed-clear acknowledgement before every dispatch and holds uncertain starts for an operator to inspect. Belt and resin printers are inventory entries.

Use the filament view’s available grams for scheduling. PrintForge debits each job’s estimated grams before dispatch and carries that deduction across restarts while Moonraker reports usage. Overestimates or refills can leave grams unavailable until you verify the balance and use **Reconcile inventory**. See [Filament inventory](docs/DEPLOYMENT.md#filament-inventory).

The architecture and API are described in [ARCHITECTURE.md](docs/ARCHITECTURE.md) and [API_CONTRACT.md](docs/API_CONTRACT.md). Local tests ran on Python 3.14. The Python 3.12 Docker image build and the real-printer pilot remain pending.

## Develop locally

Use Python 3.12, the container and CI target, and Node.js 24. From the project directory:

```sh
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m pytest
npm --prefix frontend ci
npm --prefix frontend test
npm --prefix frontend run build
python -m uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000 --workers 1
```

In PowerShell, use `& .\.venv\Scripts\Activate.ps1` for the activation step. Open [PrintForge](http://127.0.0.1:8000/) after starting the server.

## License

PrintForge is licensed under the GNU Affero General Public License, version 3 or any later version. See [LICENSE](LICENSE) for the full text. PrintForge is an original implementation. Its dependencies retain their own licenses.
