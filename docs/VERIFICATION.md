# MVP verification — 8 November 2026

This is an initial local MVP, not a hardware-qualified production release.

## Automated checks

- Backend: **26 tests passed** with Python 3.14.5. Covers authenticated API, real-mode isolation, exclusive process lock, upload validation, individual durable batch copies, profile/time/filament matching, shared-spool rejection, bed clearance, concurrent ticks, uncertain start, filename changes during upload, disconnects, restart recovery, conservative budget persistence and explicit reconciliation.
- Integration fixtures check Moonraker request paths, authentication header, upload without auto-start, explicit start, missing telemetry, Spoolman pagination and unknown weight. These exercise real adapter code with an HTTP fixture transport; no real printer was connected.
- Frontend: **2 tests passed**, TypeScript check and Vite production build passed. Tests cover demo camera suppression and disabled inventory counts.
- Docker Compose base, Portainer stack and optional Spoolman override passed configuration validation.
- Python dependencies resolved for the Python 3.12 target in a dry run; the actual Python 3.12 container was not executed.
- Independent review found and verified fixes for stale clearance during upload, delayed consumption accounting, omitted scheduling timestamps and SQLite connection cleanup.

One dependency deprecation warning remains in the Python test client: Starlette recommends a future migration from httpx to httpx2. It did not fail the tests and does not affect the Moonraker HTTP adapter.

## Browser walkthrough

Against the built frontend served by FastAPI in isolated demo mode:

1. Created a two-copy G-code batch using the form.
2. Confirmed clear bed for the matching simulated CR-10.
3. Enabled scheduling and observed automatic assignment and progress.
4. Paused and resumed the simulated print through dashboard controls.
5. Observed the completed job in history. The second copy remained queued pending new clearance.
6. Inspected the conservative 25 g filament hold and the explicit inventory-reconciliation confirmation.
7. Checked a 390 px mobile viewport: the document had no horizontal overflow (the wide queue table has its own horizontal scroll).
8. Left the scheduler paused and saved the dashboard screenshot. No browser console errors were recorded.

The temporary preview service was stopped after verification. Follow README.md to run it again. No real printer moved and no real inventory was changed.

## Pending acceptance

- Build/run the Docker image on the user's Debian 13 host; Docker Desktop's Linux engine was unavailable here.
- Connect and supervise one real Klipper/Moonraker printer, including the user's installed Moonraker version and Spoolman configuration.
- Verify camera URLs, actual spool usage, pause/cancel, service restart, disconnection and a second print after clearing the bed.
- Validate each slicer profile, nozzle and build envelope. Belt and resin workflows remain outside the first scheduler implementation.
- Optional AI, PostgreSQL, richer telemetry, users/roles, analytics and production hardening are later milestones.
